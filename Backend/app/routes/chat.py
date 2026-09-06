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
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.services.data.stats import compute_statistics
from app.services.llm.groq_service import generate_response
from app.services.web_search import search_web
from app.services.llm.langchain_pipeline import (
    PipelineOutput,
    fallback_output,
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
    started = time.monotonic()

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

    # Process user data if scope includes own_data
    rows = []
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

        try:
            rows = await execute_sql(cleaned_sql, db, table_name)
        except InvalidQueryError:
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
    else:
        # Live web has no SQL; its context comes from the search adapter below.
        cleaned_sql = None

    try:
        computed = compute_statistics(rows) if rows else {}
        prior_clarification, prior_data = await _load_prior_context(db, user.id)
        news_context = (
            await search_web(
                request.query,
                request.company_name,
                prior_clarification=prior_clarification,
            )
            if request.source_scope in ("live_web", "both")
            else None
        )
        web_sources = []
        market_data = []
        if news_context is not None:
            search_result = news_context
            news_context = search_result.context if hasattr(search_result, "context") else search_result
            web_sources = search_result.sources if hasattr(search_result, "sources") else []
            market_data = search_result.market_data if hasattr(search_result, "market_data") else []
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