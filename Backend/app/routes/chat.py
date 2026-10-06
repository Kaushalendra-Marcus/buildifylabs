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

`live_web`/`both` are implemented (live-web retrieval via `search_web`) and exercised by `test_live_web_scope_uses_retrieved_web_context`.
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
    compute_forecast,
    compute_statistics,
    infer_forecast_columns,
    is_forecast_query,
    parse_what_if,
)
from app.services.llm.groq_service import generate_response
from app.services.web_search import search_web
from app.config import get_settings
from app.services.llm.langchain_pipeline import (
    ClarificationRequest,
    PipelineOutput,
    _same_question as _is_repeat_clarification,
    coerce_web_sources,
    fallback_output,
    plan_tools,
    run_pipeline,
)
from app.services.llm.query_rewriter import wants_external_context
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

EXTERNAL_CONTEXT_CLARIFICATION_QUESTION = "Want me to also check live sources for this one?"


def _thread_id_for(request) -> str:
    """Scoped identifier for thread isolation (P0#20).

    Returns the stripped client thread id, or "" when the client sent none.
    A missing id NEVER falls back to a shared "default" bucket: sharing one
    bucket across conversations leaks prior-turn context between unrelated
    threads. The frontend always sends thread_id (activeConversationId); a
    missing value is treated by _load_prior_context as an explicit
    latest-thread-only fallback, never as cross-thread history.
    """
    try:
        thread = getattr(request, "thread_id", None) or getattr(
            request, "conversation_id", None
        )
        thread = str(thread or "").strip()
    except Exception:
        thread = ""
    return thread[:120]


async def _load_prior_context(
    db: AsyncSession, user_id: UUID, thread_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Recover the previous turn from the user's QueryLogs history.

    Returns (prior_clarification_question, prior_data_digest,
    prior_research_state, prior_query). All Nones/"" when there is no usable
    history. Fully defensive: a corrupt or foreign log row degrades to no
    context, never an error.

    Thread isolation: an explicit thread_id matches ONLY rows carrying that
    same thread (no shared "default" bucket across conversations). When the
    client sent no thread id, scope is limited to the single latest usable
    turn (latest thread only) as an explicit fallback — logged, never a
    silent cross-thread scan.

    MIGRATION SUGGESTION (perf, not behavior): thread_id currently lives
    inside the QueryLogs.response JSON (research_state.thread_id), so this
    lookup scans recent rows in Python. Once traffic warrants it, promote it
    to a real column and index it, e.g.:
        ALTER TABLE query_logs ADD COLUMN thread_id TEXT;
        CREATE INDEX ix_query_logs_user_thread
            ON query_logs (user_id, thread_id);
    See alembic/versions/c3code0000_query_logs_thread_index.py.
    """
    try:
        wanted = str(thread_id or "").strip()
        result = await db.execute(
            select(QueryLogs)
            .where(QueryLogs.user_id == user_id)
            .order_by(QueryLogs.created_at.desc())
            .limit(10)
        )
        logs = list(result.scalars().all())

        def _usable_payload(candidate) -> Optional[Dict[str, Any]]:
            if candidate is None or not candidate.response:
                return None
            try:
                payload = json.loads(candidate.response)
            except (ValueError, TypeError):
                return None
            return payload if isinstance(payload, dict) else None

        def _row_thread(payload: Dict[str, Any]) -> str:
            try:
                state = payload.get("research_state")
                if isinstance(state, dict):
                    return str(state.get("thread_id", "") or "").strip()
            except Exception:
                pass
            return ""

        log = None
        response = None
        if not wanted:
            # Explicit fallback: no thread id from the client. Use at most
            # the single latest usable turn (whatever thread it belongs to)
            # so a missing id can never pull stale context from an unrelated
            # conversation's history.
            for candidate in logs:
                payload = _usable_payload(candidate)
                if payload is None:
                    continue
                log, response = candidate, payload
                logger.info(
                    "Prior-context fallback: no thread_id, using latest "
                    "thread %r only.",
                    _row_thread(payload),
                )
                break
            if log is None or response is None:
                return None, None, None, ""
        else:
            for candidate in logs:
                payload = _usable_payload(candidate)
                if payload is None:
                    continue
                if _row_thread(payload) == wanted:
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

    Events: `{"stage": "<name>"}` progress updates, `{"text": "<delta>"}` answer
    prose chunks as the narration generates them, then exactly one
    `{"result": <PipelineOutput JSON>}`. Quota runs in the dependency, so a
    429 arrives as a regular JSON error before any event (never mid-stream).

    AsyncSession safety: the request-scoped `db` session is owned EXCLUSIVELY
    by the background `run()` task — `event_stream()` never touches it (it
    only drains the queue), so the session is never used concurrently from
    two tasks (no MissingGreenlet from cross-task sharing). Inside
    _answer_request the DB-touching evidence branches run sequentially for
    the same reason; only DB-free work (live-web search, LLM calls) overlaps.
    If this ever needs true concurrent DB access, give each branch its own
    session via the sessionmaker (e.g. `async with session_maker() as ...`)
    instead of sharing one AsyncSession across tasks.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def on_stage(stage: str) -> None:
        await queue.put({"stage": stage})

    async def on_token(text: str) -> None:
        await queue.put({"text": text})

    async def run() -> None:
        try:
            output = await _answer_request(
                db, user, request, on_stage=on_stage, on_token=on_token
            )
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
    on_token: Optional[Callable[[str], Awaitable[None]]] = None,
) -> PipelineOutput:
    """Shared answer flow for POST /chat and POST /chat/stream.

    Evidence branches (scoped SQL execution vs live-web search) run
    concurrently; the pipeline then judges, narrates, and guarantees.
    Never raises: every failure path returns a logged PipelineOutput.
    When `on_token` is given (stream endpoint only), narration prose is
    forwarded chunk by chunk as it generates; the unary endpoint passes
    nothing and behaves exactly as before.
    """
    started = time.monotonic()

    async def emit(stage: str) -> None:
        if on_stage is not None:
            try:
                await on_stage(stage)
            except Exception as exc:
                logger.warning(f"Stage callback failed: {exc}")

    # SQL text first (its result feeds execution); execution and live search
    # then run concurrently - the two slow I/Os overlap instead of stacking.
    # Prior-turn context loads before either branch: it is a cheap DB read
    # that both the tool planner and the pipeline need.
    # Capture the PK upfront: later evidence-branch rollbacks expire the ORM
    # User object, and any subsequent `user.id` access would lazy-load (sync
    # IO in async context -> MissingGreenlet 500) instead of the intended
    # logged fallback. The captured value is identical on the happy path.
    user_id = user.id
    rows: list = []
    cleaned_sql = None
    table_name = None
    thread_id = _thread_id_for(request)
    prior_clarification, prior_data, prior_research_state, prior_query = (
        await _load_prior_context(db, user_id, thread_id)
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

    # Part A: prior-turn context for follow-ups. Guard against
    # double-injection: when the clarification merge already fired
    # (plan_query != request.query), plan_query already contains the prior
    # question's text, so don't also pass raw prior_query.
    sql_prior_context = prior_query if plan_query == request.query else None

    # specs/07 FR4: a "Yes, check live sources too" quick-pick answer arrives
    # as a plain user message with scope still "own_data" (frontend doesn't
    # flip the selector) -- treat this turn as "both" for evidence gathering
    # only. request.source_scope itself is never mutated (logged as asked).
    effective_scope = (
        "both"
        if (request.query or "").strip() == "Yes, check live sources too"
        else request.source_scope
    )

    # Kill switch (specs/07 Phase 6): instant off-switch for the Tavily
    # free-tier quota, independent of a redeploy. When false, live_web/both
    # answer honestly from own_data only, never a silent full failure.
    from app.config import get_settings as _get_chat_settings

    if effective_scope in ("live_web", "both") and not _get_chat_settings().ENABLE_LIVE_WEB_SCOPE:
        logger.info("Live-web scope requested but ENABLE_LIVE_WEB_SCOPE is false; using own_data only.")
        effective_scope = "own_data"

    # specs/07 FR4: selector says own_data but the words ask for the web --
    # clarify, never silently ignore or silently override. Anti-repeat uses
    # the same _same_question convention as the pipeline's judge backstop
    # (exact + difflib fuzzy), so the follow-up "Yes..." turn never loops.
    try:
        _is_repeat = bool(
            prior_clarification
            and _is_repeat_clarification(
                prior_clarification, EXTERNAL_CONTEXT_CLARIFICATION_QUESTION
            )
        )
    except Exception:
        _is_repeat = bool(
            prior_clarification == EXTERNAL_CONTEXT_CLARIFICATION_QUESTION
        )
    if (
        request.source_scope == "own_data"
        and wants_external_context(plan_query)
        and not _is_repeat
    ):
        output = PipelineOutput(
            answer="", visuals=[], insights=[], summary="",
            root_causes=[], recommendations=[], news_context=[],
            anomalies=[], confidence=0.0,
            clarification=ClarificationRequest(
                question=EXTERNAL_CONTEXT_CLARIFICATION_QUESTION,
                options=["Yes, check live sources too", "No, just my data"],
            ),
        )
        return await _log_and_return(
            db, user_id, request.query, output, time.monotonic() - started
        )

    # For own_data or both, require user-uploaded data. Live web can work without it.
    if effective_scope in ("own_data", "both"):
        if not await _user_has_data(db, user_id):
            output = fallback_output(
                reason=(
                    "You haven't uploaded any data yet - add a CSV file to get "
                    "started, then ask me a question about it."
                )
            )
            return await _log_and_return(
                db, user_id, request.query, output, time.monotonic() - started
            )

    # Judge-directed tool routing starts now so its fast planning call
    # overlaps the SQL-generation LLM call below. plan_tools never raises
    # ([] = no opinion -> deterministic dispatch), so awaiting it is safe.
    plan_task = None
    if effective_scope in ("live_web", "both"):
        live_settings = get_settings()
        plan_task = asyncio.create_task(
            plan_tools(
                plan_query,
                source_scope=effective_scope,
                company_name=request.company_name,
                prior_clarification=prior_clarification,
                prior_query=sql_prior_context,
                has_tavily_key=bool(live_settings.WEB_SEARCH_API_KEY),
                has_fred_key=bool(live_settings.FRED_API_KEY),
            )
        )

    if effective_scope in ("own_data", "both"):
        table_name = user_data_table_name(user_id)
        try:
            columns = await get_table_columns(db, table_name)
        except Exception as exc:
            # No structured table for this user (e.g. PDF-only uploads never
            # create one): skip SQL, keep documents/web. Fail soft, never loud.
            logger.warning(f"Column introspection skipped, SQL branch off: {exc}")
            columns = []
        if not columns:
            table_name = None  # no structured data for this user; skip SQL, keep documents/web
        else:
            schema = build_data_schema(table_name, columns)
            # Canonical query (P0#1): SQL uses plan_query (clarification-merged
            # research intent), never the fragmentary request.query, so SQL and
            # research/visual timeframes cannot diverge.
            sql_prompt = build_sql_prompt(plan_query, schema, prior_query=sql_prior_context)

            sql_result = await generate_response(
                prompt=sql_prompt,
                system_prompt=SQL_SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=512,
            )
            cleaned_sql = clean_sql_response(sql_result.get("content") or "")

    sql_error: Optional[str] = None

    async def _execute_branch():
        nonlocal sql_error, cleaned_sql
        try:
            assert table_name is not None and cleaned_sql is not None
            return await execute_sql(cleaned_sql, db, table_name)
        except InvalidQueryError:
            return _Sentinel
        except HTTPException as http_exc:
            # SQL self-correction (one bounded retry): a 422 (hallucinated
            # column/table name) gets exactly one targeted regeneration with
            # the real database error + real schema fed back to the model.
            # The repaired query goes through sanitize_sql + assert_user_scoped
            # inside execute_sql unchanged — zero additional trust, just a
            # second chance at something safe AND correct. Never a loop;
            # a second failure keeps today's exact fallback path below.
            # Internal resilience only (plain logger.info, no user-facing
            # research_notes disclosure).
            if http_exc.status_code == 422 and table_name is not None:
                logger.info(
                    f"SQL execution failed (422), attempting one repair: "
                    f"{http_exc.detail}"
                )
                try:
                    real_columns = await get_table_columns(db, table_name)
                    repair_prompt = build_sql_prompt(
                        plan_query,
                        build_data_schema(table_name, real_columns),
                    ) + (
                        "\n\nYour previous query failed with this database error:\n"
                        f"{http_exc.detail}\n"
                        "Return a corrected query using only the exact column "
                        "names listed above."
                    )
                    repaired_raw = await generate_response(
                        prompt=repair_prompt,
                        system_prompt=SQL_SYSTEM_PROMPT,
                        temperature=0.2,
                        max_tokens=512,
                    )
                    repaired_sql = clean_sql_response(
                        repaired_raw.get("content") or ""
                    )
                    try:
                        rows = await execute_sql(repaired_sql, db, table_name)
                        # Traceability: the response reflects what ran.
                        cleaned_sql = repaired_sql
                        logger.info("SQL repair succeeded.")
                        return rows
                    except InvalidQueryError:
                        logger.info(
                            "SQL repair returned the INVALID_QUERY sentinel; "
                            "using the fallback path."
                        )
                    except HTTPException as retry_exc:
                        logger.info(
                            f"SQL repair also failed: {retry_exc.detail}"
                        )
                except Exception as repair_exc:
                    logger.info(f"SQL repair attempt failed: {repair_exc}")
            # SQL safety/tenant errors (422/403) are logged fallbacks
            # (P1#27), never unlogged escapes.
            sql_error = str(http_exc.detail or "query rejected")
            try:
                await db.rollback()
            except Exception:
                pass
            return _Sentinel

    async def _search_branch():
        if effective_scope not in ("live_web", "both"):
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
            prior_query=sql_prior_context,
            planned_tools=planned,
            user_id=str(user_id),
            thread_id=thread_id,
            timeframe_label=_tf,
        )

    async def _document_branch():
        if effective_scope not in ("own_data", "both"):
            return [], []
        from app.services.data.vector_store import retrieve_document_evidence
        return await retrieve_document_evidence(
            db, user_id, plan_query, request.file_ids
        )

    await emit("evidence")
    try:
        # Session safety: AsyncSession forbids concurrent operations on one
        # session object. _execute_branch and _document_branch both touch
        # `db`, so they MUST NOT run concurrently via a single gather —
        # that is the MissingGreenlet / "already executing" failure mode.
        # _search_branch is DB-free (LLM planning + HTTP retrieval over
        # captured strings), so it may safely overlap ONE db branch at a
        # time. Sequence: (exec + search concurrently), then documents.
        if table_name is not None:
            exec_result, search_result = await asyncio.gather(
                _execute_branch(),
                _search_branch(),
            )
        else:
            exec_result = []
            search_result = await _search_branch()
        doc_result = await _document_branch()
        document_context, document_sources = doc_result
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
        _rs.update({
            "canonical_query": plan_query,
            "thread_id": thread_id,
            "scope_requested": request.source_scope,
            "scope_effective": effective_scope,
            "scope_downgraded": bool(request.source_scope != effective_scope),
        })
        output.research_state = _rs
        return await _log_and_return(
            db, user_id, plan_query, output, time.monotonic() - started
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
        _rs0.update({
            "canonical_query": plan_query,
            "thread_id": thread_id,
            "scope_requested": request.source_scope,
            "scope_effective": effective_scope,
            "scope_downgraded": bool(request.source_scope != effective_scope),
        })
        output.research_state = _rs0
        return await _log_and_return(
            db, user_id, plan_query, output, time.monotonic() - started
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
            # specs/11 §3.2 v1: a forecast-phrased question ("forecast next
            # month's revenue") extrapolates deterministically from the
            # executed rows; the LLM narrates the precomputed numbers, never
            # its own trend math (same pattern as what-if above).
            try:
                if is_forecast_query(plan_query):
                    date_col, value_col = infer_forecast_columns(rows)
                    if date_col and value_col:
                        forecast = compute_forecast(rows, date_col, value_col)
                        if forecast is not None:
                            computed = {**computed, "forecast": forecast}
                        else:
                            # Deterministic dead-end, recorded structurally:
                            # too few parseable history points for a trend.
                            # The pipeline forces confidence 0 + an explicit
                            # missing-data sentence from this (never LLM
                            # invention).
                            computed = {
                                **computed,
                                "forecast_unavailable": {
                                    "reason": "insufficient_history",
                                    "detail": (
                                        "Not enough dated history in your "
                                        "data to project a trend."
                                    ),
                                },
                            }
                    else:
                        # Ambiguous value axis (no single numeric column, or
                        # no date axis): ask-don't-guess — record the miss
                        # instead of picking a column to forecast.
                        computed = {
                            **computed,
                            "forecast_unavailable": {
                                "reason": "ambiguous_columns",
                                "detail": (
                                    "Your data has no single "
                                    "date-plus-value series I can "
                                    "unambiguously project."
                                ),
                            },
                        }
            except Exception as exc:
                logger.warning(f"Forecast computation skipped: {exc}")
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
        # Reserved sub-budget for documents, then remaining budget for web:
        # your own uploaded document is inherently more trustworthy than a
        # generic web result and must never be crowded out by a
        # coincidentally-well-scored web snippet. Both pools are ranked
        # best-first BEFORE the budget cut so the budget keeps the
        # best-ranked pairs, never merely the first-arrived ones.
        from app.services.llm.context_budget import (
            fit_pairs_to_budget,
            rank_snippet_pairs,
        )

        _settings = get_settings()
        try:
            document_context, document_sources = rank_snippet_pairs(
                document_context or [], document_sources or [], plan_query
            )
        except Exception:
            pass
        _doc_budget = min(_settings.MAX_DOCUMENT_CONTEXT_CHARS, _settings.MAX_EVIDENCE_CONTEXT_CHARS)
        document_context, document_sources, _ = fit_pairs_to_budget(
            document_context, document_sources, _doc_budget
        )
        try:
            news_context, web_sources = rank_snippet_pairs(
                news_context or [], web_sources or [], plan_query
            )
        except Exception:
            news_context = news_context or []
            web_sources = web_sources or []
        _remaining_budget = max(
            0, _settings.MAX_EVIDENCE_CONTEXT_CHARS - sum(len(t) for t in document_context)
        )
        news_context, web_sources, _ = fit_pairs_to_budget(
            news_context or [], web_sources or [], _remaining_budget
        )
        news_context = document_context + news_context
        web_sources = document_sources + web_sources
        output = await run_pipeline(
            user_query=request.query,
            db_data=rows,
            computed_numbers=computed,
            news_context=news_context,
            source_scope=effective_scope,
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
            on_token=on_token,
            documents_scoped=bool(request.file_ids),
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
        # Contract: retrieval dicts -> validated WebSource objects so the
        # API response matches PipelineOutput (no serializer warnings) and
        # the frontend sources section always has title/url/provider.
        try:
            output.web_sources = coerce_web_sources(web_sources)
        except Exception:
            pass
        try:
            _rsf = dict(output.research_state or {})
        except Exception:
            _rsf = {}
        _rsf.update({
            "canonical_query": plan_query,
            "thread_id": thread_id,
            "total_rows": len(rows or []),
            # Kill-switch disclosure: when live_web was requested but served
            # from own_data, record both so the answer is traceable.
            "scope_requested": request.source_scope,
            "scope_effective": effective_scope,
            "scope_downgraded": bool(request.source_scope != effective_scope),
        })
        output.research_state = _rsf
        # Scope-downgrade thinking disclosure: the TrustFooter notice
        # ("Live web unavailable, answered from your data") is driven by
        # research_state.scope_downgraded; record the same fact in thinking
        # so the trace shows its work instead of silently switching scope.
        try:
            if (
                request.source_scope in ("live_web", "both")
                and effective_scope == "own_data"
            ):
                output.thinking = list(output.thinking or []) + [
                    "Scope downgraded: live web unavailable, answered from your data."
                ]
        except Exception:
            pass

    # QueryLogs preserve the canonical query (P0#1); DB-logging failure must
    # not turn a valid response into an unrelated 500 where avoidable.
    try:
        return await _log_and_return(
            db, user_id, plan_query, output, time.monotonic() - started
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

    Feedback-loop note: QueryLogs carries a suggested (unmigrated, never
    written) `flagged_reason` column for a future free-text reason alongside
    this boolean. Wiring it needs an Alembic migration first — until then
    this endpoint intentionally persists only `flagged=True`.
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