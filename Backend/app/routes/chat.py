"""The first end-to-end `POST /chat` route (Phase B4, master plan step 6).

Flow: `rate_limiter` (quota) -> prior-turn context (last QueryLogs row) ->
real per-user schema -> SQL prompt -> LLM -> `clean_sql_response` ->
`sanitize_sql` (inside `execute_sql`) -> user-scoped `execute_sql` ->
deterministic pandas stats (specs/11 §3.1) -> decision-driven `run_pipeline`
(judge -> narrate -> visual guarantee) -> `PipelineOutput`.

Trust requirements (specs/10 §2) are built in, not retrofitted:
- every answer carries the **exact SQL + raw row slice** (traceability) and the
QueryLogs id that produced it, so the UI's "show the query"/flag are real;
- hedged causal language is enforced in the pipeline's SYSTEM_PROMPT;
- **every query+response pair is written to QueryLogs** (including graceful
fallbacks), with a flag endpoint (`POST /chat/flag`) feeding it;
- the `clarification` alternate-response mode is live via the pipeline's
ask-don't-guess prompt path, with an anti-repeat backstop so a follow-up
never gets the same question twice.

MVP `source_scope` = `own_data` only; `live_web`/`both` are deferred to B7.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.file_upload import FileUpload
from app.db.models.query_logs import QueryLogs
from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user
from app.middlewares.rate_limiter import rate_limiter
from app.schemas.chat import ChatRequest, FlagRequest, FlagResponse
from app.services.data.executor import (
    InvalidQueryError,
    execute_sql,
    get_table_columns,
    user_data_table_name,
)
from app.services.data.stats import (
    apply_what_if,
    compute_statistics,
    parse_what_if,
)
from app.services.llm.groq_service import generate_response
from app.services.web_search import search_web
from app.config import get_settings
from app.services.llm.langchain_pipeline import (
    PipelineOutput,
    fallback_output,
    plan_tools,
    run_pipeline,
)
from app.services.llm.sql_generator import (
    SQL_SYSTEM_PROMPT,
    build_data_schema,
    build_sql_prompt,
    clean_sql_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

# Upper bound on the raw row slice echoed back in the response (data_preview).
# The model's answer already summarizes the full set, so this stays lean.
DATA_PREVIEW_MAX_ROWS = 50

# Prior-turn digest budget: enough rows for follow-ups ("chart that") without
# bloating the prompt.
PRIOR_DATA_MAX_ROWS = 8


async def _load_prior_context(
    db: AsyncSession, user_id: UUID
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Recover the previous turn from the user's latest QueryLogs row.

    Returns (prior_clarification_question, prior_data_digest). Both are None
    when there is no usable history. Fully defensive: a corrupt or foreign
    log row degrades to no context, never an error.
    """
    try:
        result = await db.execute(
            select(QueryLogs)
            .where(QueryLogs.user_id == user_id)
            .order_by(QueryLogs.created_at.desc())
            .limit(1)
        )
        log = result.scalar_one_or_none()
        if log is None or not log.response:
            return None, None
        try:
            response = json.loads(log.response)
        except (ValueError, TypeError):
            return None, None
        if not isinstance(response, dict):
            return None, None

        prior_clarification = None
        clarification = response.get("clarification")
        if isinstance(clarification, dict) and clarification.get("question"):
            prior_clarification = str(clarification["question"])

        prior_data = None
        preview = response.get("data_preview")
        if isinstance(preview, list) and preview:
            sample = [row for row in preview[:PRIOR_DATA_MAX_ROWS] if isinstance(row, dict)]
            if sample:
                prior_data = {
                    "columns": list(sample[0].keys()),
                    "row_count": len(preview),
                    "rows": sample,
                    "from_query": log.query,
                }
        return prior_clarification, prior_data
    except Exception as exc:
        logger.warning(f"Prior context unavailable, continuing without it: {exc}")
        return None, None


async def _user_has_data(db: AsyncSession, user_id: UUID) -> bool:
    result = await db.execute(
        select(FileUpload.id)
        .where(
            FileUpload.user_id == user_id,
            FileUpload.status == "completed",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _log_and_return(
    db: AsyncSession,
    user_id: UUID,
    query_text: str,
    output: PipelineOutput,
    execution_time: float,
) -> PipelineOutput:
    """Persist every query+response pair to QueryLogs (specs/10 §2).

    Writes the full PipelineOutput (incl. sql_query/data_preview) as the
    response, then stamps the returned output with the log id so the UI can
    flag this exact answer.
    """
    log = QueryLogs(
        user_id=user_id,
        query=query_text,
        response=output.model_dump_json(),
        execution_time=round(execution_time, 3),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    output.query_log_id = str(log.id)
    return output


@router.post("", response_model=PipelineOutput)
async def chat(
    request: ChatRequest,
    user: User = Depends(rate_limiter),
    db: AsyncSession = Depends(get_db),
):
    return await _answer_request(db, user, request)


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: User = Depends(rate_limiter),
    db: AsyncSession = Depends(get_db),
):
    """Same answer as POST /chat, streamed as server-sent events.

    Events: `{"stage": "<name>"}` progress updates, then exactly one
    `{"result": <PipelineOutput JSON>}`. Quota runs in the dependency, so a
    429 arrives as a regular JSON error before any event (never mid-stream).
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def on_stage(stage: str) -> None:
        await queue.put({"stage": stage})

    async def run() -> None:
        try:
            output = await _answer_request(db, user, request, on_stage=on_stage)
            await queue.put({"result": json.loads(output.model_dump_json())})
        except Exception as exc:  # pragma: no cover - _answer_request never raises
            logger.error(f"Stream flow crashed: {exc}")
            await queue.put({"error": "Something went wrong. Please try again."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def event_stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class _Sentinel:
    """Marker for the INVALID_QUERY short-circuit inside the gather."""


async def _answer_request(
    db: AsyncSession,
    user: User,
    request: ChatRequest,
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
) -> PipelineOutput:
    """Shared answer flow for POST /chat and POST /chat/stream.

    Evidence branches (scoped SQL execution vs live-web search) run
    concurrently; the pipeline then judges, narrates, and guarantees.
    Never raises: every failure path returns a logged PipelineOutput.
    """
    started = time.monotonic()

    async def emit(stage: str) -> None:
        if on_stage is not None:
            try:
                await on_stage(stage)
            except Exception as exc:
                logger.warning(f"Stage callback failed: {exc}")

    # For own_data or both, require user-uploaded data. Live web can work without it.
    if request.source_scope in ("own_data", "both"):
        if not await _user_has_data(db, user.id):
            output = fallback_output(
                reason=(
                    "You haven't uploaded any data yet - add a CSV file to get "
                    "started, then ask me a question about it."
                )
            )
            return await _log_and_return(
                db, user.id, request.query, output, time.monotonic() - started
            )

    # SQL text first (its result feeds execution); execution and live search
    # then run concurrently - the two slow I/Os overlap instead of stacking.
    # Prior-turn context loads before either branch: it is a cheap DB read
    # that both the tool planner and the pipeline need.
    rows: list = []
    cleaned_sql = None
    table_name = None
    prior_clarification, prior_data = await _load_prior_context(db, user.id)

    # Judge-directed tool routing starts now so its fast planning call
    # overlaps the SQL-generation LLM call below. plan_tools never raises
    # ([] = no opinion -> deterministic dispatch), so awaiting it is safe.
    plan_task = None
    if request.source_scope in ("live_web", "both"):
        live_settings = get_settings()
        plan_task = asyncio.create_task(
            plan_tools(
                request.query,
                source_scope=request.source_scope,
                company_name=request.company_name,
                prior_clarification=prior_clarification,
                has_tavily_key=bool(live_settings.WEB_SEARCH_API_KEY),
                has_fred_key=bool(live_settings.FRED_API_KEY),
            )
        )

    if request.source_scope in ("own_data", "both"):
        table_name = user_data_table_name(user.id)
        columns = await get_table_columns(db, table_name)
        schema = build_data_schema(table_name, columns)
        sql_prompt = build_sql_prompt(request.query, schema)

        sql_result = await generate_response(
            prompt=sql_prompt,
            system_prompt=SQL_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=512,
        )
        cleaned_sql = clean_sql_response(sql_result.get("content") or "")

    async def _execute_branch():
        try:
            assert table_name is not None and cleaned_sql is not None
            return await execute_sql(cleaned_sql, db, table_name)
        except InvalidQueryError:
            return _Sentinel

    async def _search_branch():
        if request.source_scope not in ("live_web", "both"):
            return None
        planned = await plan_task if plan_task is not None else None
        return await search_web(
            request.query,
            request.company_name,
            prior_clarification=prior_clarification,
            planned_tools=planned,
        )

    await emit("evidence")
    try:
        exec_result, search_result = await asyncio.gather(
            _execute_branch() if table_name is not None else asyncio.sleep(0, result=[]),
            _search_branch(),
        )
    except Exception:
        # A branch failure outside the sentinel contract must not orphan the
        # planner task (pending-task noise at shutdown); nothing else changes
        # - the exception still propagates as before this task existed.
        if plan_task is not None and not plan_task.done():
            plan_task.cancel()
        raise
    if exec_result is _Sentinel:
        # specs/05 §5.3 + §6: the sentinel short-circuits to a graceful message,
        # never returned as if it were data; still logged so failures show up.
        output = fallback_output(
            reason=(
                "I couldn't turn that into a query for your data - try "
                "rephrasing the question."
            )
        )
        return await _log_and_return(
            db, user.id, request.query, output, time.monotonic() - started
        )
    rows = exec_result

    try:
        computed = compute_statistics(rows) if rows else {}
        # specs/11 §3.3 v1: a price-scenario question ("what if I raise
        # price 10%") recomputes deterministically from the executed rows;
        # the LLM narrates the precomputed numbers, never its own arithmetic.
        if rows:
            try:
                scenario = parse_what_if(request.query)
                if scenario is not None:
                    what_if = apply_what_if(rows, *scenario)
                    if what_if is not None:
                        computed = {**computed, "what_if": what_if}
            except Exception as exc:
                logger.warning(f"What-if scenario skipped: {exc}")
        news_context = None
        web_sources = []
        market_data = []
        fundamentals = []
        macro_data = []
        price_history = []
        financial_history = []
        research_notes = []
        if search_result is not None:
            news_context = (
                search_result.context
                if hasattr(search_result, "context")
                else search_result
            )
            web_sources = (
                search_result.sources if hasattr(search_result, "sources") else []
            )
            market_data = (
                search_result.market_data
                if hasattr(search_result, "market_data")
                else []
            )
            fundamentals = (
                getattr(search_result, "fundamentals", []) or []
            )
            macro_data = (
                getattr(search_result, "macro_data", []) or []
            )
            price_history = (
                getattr(search_result, "price_history", []) or []
            )
            financial_history = (
                getattr(search_result, "financial_history", []) or []
            )
            research_notes = (
                getattr(search_result, "research_notes", []) or []
            )
            # Structured macro series chart like market series downstream.
            if macro_data:
                market_data = list(market_data) + list(macro_data)
            retrieved_at = datetime.now(timezone.utc).isoformat()
            web_sources = [
                {**source, "retrieved_at": retrieved_at} for source in web_sources
            ]
        output = await run_pipeline(
            user_query=request.query,
            db_data=rows,
            computed_numbers=computed,
            news_context=news_context,
            source_scope=request.source_scope,
            company_name=request.company_name,
            prior_clarification=prior_clarification,
            prior_data=prior_data,
            market_data=market_data,
            web_sources=web_sources,
            on_stage=on_stage,
            fundamentals=fundamentals,
            macro_data=macro_data,
            price_history=price_history,
            financial_history=financial_history,
            research_notes=research_notes,
        )
    except Exception as exc:
        # Never let the pipeline crash the request: fall back per specs/06 FR4.
        logger.error(f"Pipeline crashed in /chat: {exc}")
        output = fallback_output(
            reason="I ran into a problem answering that - please try again.",
            confidence=0.0,
        )
    else:
        if cleaned_sql:
            output.sql_query = cleaned_sql
        output.data_preview = rows[:DATA_PREVIEW_MAX_ROWS] if rows else []
        output.web_sources = web_sources

    return await _log_and_return(
        db, user.id, request.query, output, time.monotonic() - started
    )


@router.post("/flag", response_model=FlagResponse)
async def flag_answer(
    body: FlagRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flag an answer as wrong/misleading - lands on its QueryLogs row (§10 §2).

    Own-only: another user's log id is indistinguishable from a missing one
    (404), so flagging can't probe or touch another user's data.
    """
    result = await db.execute(
        update(QueryLogs)
        .where(QueryLogs.id == body.query_log_id, QueryLogs.user_id == user.id)
        .values(flagged=True)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Query log not found",
        )
    await db.commit()
    return FlagResponse(query_log_id=body.query_log_id, flagged=True)