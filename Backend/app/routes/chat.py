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


def _thread_id_for(request) -> str:
    """Minimum scoped identifier for thread isolation (P0#20)."""
    try:
        thread = getattr(request, "thread_id", None) or getattr(
            request, "conversation_id", None
        )
        thread = str(thread or "default").strip() or "default"
    except Exception:
        thread = "default"
    return thread[:120]


async def _load_prior_context(
    db: AsyncSession, user_id: UUID, thread_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Recover the previous turn from the user's latest QueryLogs row.

    Returns (prior_clarification_question, prior_data_digest,
    prior_research_state, prior_query). All Nones/"" when there is no usable
    history. Fully defensive: a corrupt or foreign log row degrades to no
    context, never an error.
    """
    try:
        wanted = str(thread_id or "default").strip() or "default"
        result = await db.execute(
            select(QueryLogs)
            .where(QueryLogs.user_id == user_id)
            .order_by(QueryLogs.created_at.desc())
            .limit(10)
        )
        logs = list(result.scalars().all())
        log = None
        response = None
        for candidate in logs:
            if candidate is None or not candidate.response:
                continue
            try:
                payload = json.loads(candidate.response)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            state = payload.get("research_state")
            row_thread = (
                str((state or {}).get("thread_id", "") or "").strip()
                if isinstance(state, dict)
                else ""
            ) or "default"
            if row_thread == wanted:
                log = candidate
                response = payload
                break
        if log is None or response is None:
            return None, None, None, ""

        prior_clarification = None
        clarification = response.get("clarification")
        if isinstance(clarification, dict) and clarification.get("question"):
            prior_clarification = str(clarification["question"])

        prior_data = None
        preview = response.get("data_preview")
        total_rows = None
        try:
            _rs = response.get("research_state")
            if isinstance(_rs, dict) and isinstance(_rs.get("total_rows"), int):
                total_rows = int(_rs["total_rows"])
        except Exception:
            total_rows = None
        if isinstance(preview, list) and preview:
            sample = [row for row in preview[:PRIOR_DATA_MAX_ROWS] if isinstance(row, dict)]
            if sample:
                prior_data = {
                    "columns": list(sample[0].keys()),
                    "row_count": total_rows if total_rows is not None else len(preview),
                    "rows": sample,
                    "from_query": log.query,
                }
        # Compact structured research state (Phase 18): the previous turn's
        # canonical plan + validated evidence summary, so a follow-up keeps
        # its entities/metrics/period instead of starting from a fragment.
        prior_research_state = response.get("research_state")
        if not isinstance(prior_research_state, dict):
            prior_research_state = None
        return prior_clarification, prior_data, prior_research_state, str(log.query or "")
    except Exception as exc:
        logger.warning(f"Prior context unavailable, continuing without it: {exc}")
        return None, None, None, ""


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
    thread_id = _thread_id_for(request)
    prior_clarification, prior_data, prior_research_state, prior_query = (
        await _load_prior_context(db, user.id, thread_id)
    )

    # Phase 3: a clarification reply is a fragment of the ORIGINAL research
    # plan, not a standalone query. Merge it back so decomposition keeps the
    # original entities/metrics/period (planning/research use the merged
    # text; narration still answers the user's literal message).
    plan_query = request.query
    if prior_clarification and prior_query:
        try:
            from app.services.data.comparison import (
                merge_clarification_context as _merge_ctx,
            )
            plan_query = _merge_ctx(prior_query, prior_clarification, request.query)
        except Exception as exc:
            logger.warning(f"Clarification merge failed: {exc}")
            plan_query = request.query

    # Judge-directed tool routing starts now so its fast planning call
    # overlaps the SQL-generation LLM call below. plan_tools never raises
    # ([] = no opinion -> deterministic dispatch), so awaiting it is safe.
    plan_task = None
    if request.source_scope in ("live_web", "both"):
        live_settings = get_settings()
        plan_task = asyncio.create_task(
            plan_tools(
                plan_query,
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
        # Canonical query (P0#1): SQL uses plan_query (clarification-merged
        # research intent), never the fragmentary request.query, so SQL and
        # research/visual timeframes cannot diverge.
        sql_prompt = build_sql_prompt(plan_query, schema)

        sql_result = await generate_response(
            prompt=sql_prompt,
            system_prompt=SQL_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=512,
        )
        cleaned_sql = clean_sql_response(sql_result.get("content") or "")

    sql_error: Optional[str] = None

    async def _execute_branch():
        nonlocal sql_error
        try:
            assert table_name is not None and cleaned_sql is not None
            return await execute_sql(cleaned_sql, db, table_name)
        except InvalidQueryError:
            return _Sentinel
        except HTTPException as http_exc:
            # SQL safety/tenant errors (422/403) are logged fallbacks
            # (P1#27), never unlogged escapes.
            sql_error = str(http_exc.detail or "query rejected")
            try:
                await db.rollback()
            except Exception:
                pass
            return _Sentinel

    async def _search_branch():
        if request.source_scope not in ("live_web", "both"):
            return None
        planned = await plan_task if plan_task is not None else None
        try:
            from app.services.data.comparison import parse_time_range as _ptr2

            _tf = (_ptr2(plan_query or "").label or "") or None
        except Exception:
            _tf = None
        return await search_web(
            plan_query,
            request.company_name,
            prior_clarification=prior_clarification,
            planned_tools=planned,
            user_id=str(user.id),
            thread_id=thread_id,
            timeframe_label=_tf,
        )

    await emit("evidence")
    try:
        exec_result, search_result = await asyncio.gather(
            _execute_branch() if table_name is not None else asyncio.sleep(0, result=[]),
            _search_branch(),
        )
    except Exception as branch_exc:
        # Consistent failure contract (P1#27): branch failures outside the
        # sentinel contract are logged to QueryLogs as fallbacks, never
        # propagated as 500s and never escaping without a log row.
        if plan_task is not None and not plan_task.done():
            plan_task.cancel()
        logger.error(f"Evidence branch failed in /chat: {branch_exc}")
        try:
            await db.rollback()
        except Exception:
            pass
        output = fallback_output(
            reason="I ran into a problem gathering evidence - please try again.",
            confidence=0.0,
        )
        try:
            _rs = dict(output.research_state or {})
        except Exception:
            _rs = {}
        _rs.update({"canonical_query": plan_query, "thread_id": thread_id})
        output.research_state = _rs
        return await _log_and_return(
            db, user.id, plan_query, output, time.monotonic() - started
        )
    if exec_result is _Sentinel:
        # specs/05 §5.3 + §6: the sentinel short-circuits to a graceful message,
        # never returned as if it were data; still logged so failures show up.
        output = fallback_output(
            reason=(
                sql_error
                or "I couldn't turn that into a query for your data - try "
                "rephrasing the question."
            )
        )
        try:
            _rs0 = dict(output.research_state or {})
        except Exception:
            _rs0 = {}
        _rs0.update({"canonical_query": plan_query, "thread_id": thread_id})
        output.research_state = _rs0
        return await _log_and_return(
            db, user.id, plan_query, output, time.monotonic() - started
        )
    rows = exec_result

    try:
        computed = compute_statistics(rows) if rows else {}
        # specs/11 §3.3 v1: a price-scenario question ("what if I raise
        # price 10%") recomputes deterministically from the executed rows;
        # the LLM narrates the precomputed numbers, never its own arithmetic.
        if rows:
            try:
                scenario = parse_what_if(plan_query)
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
        macro_note = ""
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
            macro_note = (
                getattr(search_result, "macro_note", "") or ""
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
            # Channels stay separate (P0#15): macro_data is NEVER merged
            # into market_data (a market chart must never contain CPI/GDP).
            retrieved_at = datetime.now(timezone.utc).isoformat()
            web_sources = [
                {
                    # retrieved_at is transport metadata; the source's own
                    # publication date (published_date/source_published_at)
                    # is never overwritten (P1#28).
                    **{k: v for k, v in source.items() if k != "retrieved_at"},
                    "retrieved_at": retrieved_at,
                }
                for source in web_sources
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
            macro_note=macro_note,
            price_history=price_history,
            financial_history=financial_history,
            research_notes=research_notes,
            prior_research_state=prior_research_state,
            plan_query=plan_query,
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
        try:
            _rsf = dict(output.research_state or {})
        except Exception:
            _rsf = {}
        _rsf.update({
            "canonical_query": plan_query,
            "thread_id": thread_id,
            "total_rows": len(rows or []),
        })
        output.research_state = _rsf

    # QueryLogs preserve the canonical query (P0#1); DB-logging failure must
    # not turn a valid response into an unrelated 500 where avoidable.
    try:
        return await _log_and_return(
            db, user.id, plan_query, output, time.monotonic() - started
        )
    except Exception as log_exc:
        logger.error(f"QueryLogs write failed, returning unlogged answer: {log_exc}")
        try:
            await db.rollback()
        except Exception:
            pass
        return output


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