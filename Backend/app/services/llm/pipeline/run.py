"""run_pipeline: judge -> narrate -> ground -> guarantee. Split from langchain_pipeline.py; behavior unchanged."""

import json
import logging
import re

from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence
from pydantic import ValidationError
from app.config import get_settings

from .shared import call_history_gate, call_llm, call_llm_stream, call_research_completeness, has_research_completeness
from .models import Decision, PipelineOutput, SOURCE_SCOPES, VisualOutput
from .prompts import CHART_INTENT_RE, PROSE_RESCUE_SYSTEM_PROMPT, SYSTEM_PROMPT
from .deps import build_research_plan, build_trace, clarification_asks_for_researchable_data, evidence_driven_confidence, format_runtime_trace, must_not_clarify, reconcile_judge_tools
from .prompting import _same_question, build_prompt, detect_preferred_visual, extract_json, fallback_output, normalize_pipeline_payload, sanitize_citations
from .judge import judge_sufficiency
from .history import _fail_closed_gate, compute_structured_what_if, is_what_if_query
from .grounding import apply_narration_contract, build_validated_evidence_state, drop_ungrounded_visuals_evidence, is_visual_stale_for_query, plan_visuals_from_evidence
from .guarantee import ensure_visuals
from app.services.llm.groq_service import _to_strict_schema

logger = logging.getLogger(__name__)

# Strict-schema payload for narration, built once from the PipelineOutput
# contract (Groq constrained decoding — see judge.py for fallback behavior).
_PIPELINE_STRICT_SCHEMA = _to_strict_schema(PipelineOutput)



def log_runtime_trace(trace: Dict[str, Any]) -> None:
    """Emit one safe structured runtime-trace line (H15)."

    Never logs payloads, keys, tokens, or rows -- only the trace contract
    (counts, names, gate verdict, confidence). Safe to leave on in prod.
    """
    try:
        if format_runtime_trace is not None:
            logger.info("RUNTIME TRACE %s", format_runtime_trace(trace))
        else:
            logger.info(
                "RUNTIME TRACE query=%r gate=%s visuals=%s confidence=%s",
                str((trace or {}).get("query", ""))[:120],
                (trace or {}).get("comparison_gate"),
                (trace or {}).get("visual_decision"),
                (trace or {}).get("final_confidence"),
            )
    except Exception as exc:
        logger.warning("Runtime trace log failed: %s", exc)


async def run_pipeline(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: Optional[dict] = None,
    news_context: Optional[list] = None,
    source_scope: SOURCE_SCOPES = "own_data",
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
    prior_data: Optional[Dict[str, Any]] = None,
    market_data: Optional[list] = None,
    web_sources: Optional[list] = None,
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    macro_note: Optional[str] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    research_notes: Optional[list] = None,
    prior_research_state: Optional[Dict[str, Any]] = None,
    plan_query: Optional[str] = None,
) -> PipelineOutput:
    """Decision-driven pipeline: judge -> narrate -> ground -> guarantee (specs/06).

    1. The sufficiency judge (LLM) decides answer-vs-clarify and plans visuals
       from the actual tool outputs on hand.
    2. The narration call answers following that verdict, with prior-turn
       context so follow-ups resolve instead of looping. Live-web narration
       routes through the stronger model (specs/12 synthesis touchpoint).
    3. LLM-proposed web visuals are grounding-checked: any numeric visual
       whose numbers don't appear in the cited snippets is discarded for the
       deterministic figures fallback (checked trust, not implicit).
    4. The deterministic visual guarantee fills visuals from real rows/series
       when a validated answer arrives naked.
    5. A repeat-clarification backstop re-asks the narrator once with
       clarification disabled, so the user never gets the same question twice.

    Deterministic numbers are always precomputed by the caller and passed in;
    this function only narrates them. Falls back to a low-confidence generic
    PipelineOutput (never raises) on any malformed/validation failure
    (specs/06 FR4).
    """
    if news_context is None:
        news_context = []
    if computed_numbers is None:
        computed_numbers = {}
    if market_data is None:
        market_data = []
    if web_sources is None:
        web_sources = []
    if fundamentals is None:
        fundamentals = []
    if macro_data is None:
        macro_data = []
    if macro_note is None:
        macro_note = ""
    if price_history is None:
        price_history = []
    if financial_history is None:
        financial_history = []
    if research_notes is None:
        research_notes = []

    rows = list(db_data or [])
    thinking: List[str] = []
    # Deterministic what-if (generic): structured-history scenario first,
    # then the row-level price scenario for own-data queries. Both compute
    # in code; narration quotes verbatim (never LLM arithmetic).
    try:
        if is_what_if_query(plan_query or user_query) and not (computed_numbers or {}).get("what_if"):
            _structured = compute_structured_what_if(
                financial_history, plan_query or user_query
            )
            if _structured is not None:
                computed_numbers = {**(computed_numbers or {}), "what_if": _structured}
                thinking.append(
                    f"Deterministic what-if computed: {str(_structured.get('pct_change'))}% "
                    f"(factor {str(_structured.get('factor'))})."
                )
            elif rows:
                try:
                    from app.services.data.stats import (
                        apply_what_if as _apply_wif,
                        parse_what_if as _parse_wif,
                    )

                    _scenario = _parse_wif(plan_query or user_query)
                    if _scenario is not None:
                        _row_wif = _apply_wif(rows, *_scenario)
                        if _row_wif is not None:
                            computed_numbers = {**(computed_numbers or {}), "what_if": _row_wif}
                            thinking.append(
                                f"Deterministic row what-if computed: "
                                f"{str(_row_wif.get('pct_change'))}%."
                            )
                except Exception as _exc:
                    logger.warning("Row what-if skipped: %s", _exc)
            if not (computed_numbers or {}).get("what_if"):
                # No uploaded data at all (or it didn't yield a scenario):
                # the question may state its own baseline numbers directly
                # ("5,000 customers paying $100/month. If price +25%...").
                # Compute that deterministically too -- never let the LLM
                # do this arithmetic silently just because there's no data
                # table to scale from.
                try:
                    from app.services.data.stats import (
                        compute_freeform_scenario as _compute_freeform,
                    )

                    _freeform = _compute_freeform(plan_query or user_query)
                    if _freeform is not None:
                        computed_numbers = {**(computed_numbers or {}), "what_if": _freeform}
                        thinking.append(
                            f"Deterministic freeform scenario computed: "
                            f"{str(_freeform.get('pct_change'))}% revenue change "
                            f"from stated baseline (no uploaded data used)."
                        )
                except Exception as _exc:
                    logger.warning("Freeform scenario skipped: %s", _exc)
    except Exception as exc:
        logger.warning("Structured what-if skipped: %s", exc)
    for note in research_notes:
        thinking.append(f"Research: {str(note)[:200]}")

    async def emit(stage: str) -> None:
        if on_stage is not None:
            try:
                await on_stage(stage)
            except Exception as exc:
                logger.warning(f"Stage callback failed: {exc}")

    async def _rescue_or_fallback(reason: str) -> PipelineOutput:
        """Prose rescue over real evidence, else the honest generic fallback."""
        rescued = await _narrate_prose_rescue(
            user_query, rows, news_context, market_data
        )
        if rescued is not None:
            rescued.thinking = thinking + ["Structured narration failed; prose rescue."]
            return ensure_visuals(
                rescued,
                rows=rows,
                computed_numbers=computed_numbers,
                market_data=market_data,
                web_sources=web_sources,
                news_context=news_context,
                preferred_visual=None,
                query=user_query,
                fundamentals=fundamentals,
                macro_data=macro_data,
                price_history=price_history,
                financial_history=financial_history,
            )
        return fallback_output(reason=reason, confidence=0.0)

    # The canonical ResearchPlan is the single source of truth for this
    # request (Phase 1): decomposition -> entities/metrics/time-range ->
    # required tools. Every downstream stage (judge evidence, gate,
    # confidence, visuals, research_state) reads from it -- never from a
    # competing ad-hoc decomposition. plan_query carries the ORIGINAL
    # research intent when this turn answers a clarification (Phase 3);
    # narration still uses the user's literal message.
    plan_dict: Dict[str, Any] = {}
    plan_text = plan_query or user_query
    try:
        if build_research_plan is not None:
            plan_dict = dict(build_research_plan(plan_text, source_scope=source_scope) or {})
    except Exception as exc:
        logger.warning("Canonical research plan failed: %s", exc)
        plan_dict = {}
    # Prior research reuse is explicit + validated (P0#20): when the caller
    # passes the previous turn's structured state, compare its canonical
    # structure with the current plan. A structural match records reuse;
    # any entity/metric/timeframe change records non-reuse (fresh evidence
    # only). Research is still recomputed from current retrieval -- this
    # never injects stale rows, it only documents continuity honestly.
    try:
        if isinstance(prior_research_state, dict):
            _prior_plan = (prior_research_state.get("plan") or {})
            _prior_ents = {str(e).strip().lower() for e in (_prior_plan.get("entities", []) or [])}
            _cur_ents = {str(e).strip().lower() for e in (plan_dict.get("entities", []) or [])}
            _prior_mets = {str(m).strip().lower() for m in (_prior_plan.get("metrics", []) or [])}
            _cur_mets = {str(m).strip().lower() for m in (plan_dict.get("metrics", []) or [])}
            _prior_tf = str((_prior_plan.get("time_range", {}) or {}).get("label", "") or "")
            _cur_tf = str((plan_dict.get("time_range", {}) or {}).get("label", "") or "")
            if _prior_ents == _cur_ents and _prior_mets == _cur_mets and _prior_tf == _cur_tf:
                thinking.append("Prior research structure matches; continuity validated.")
            else:
                thinking.append("Prior research structure differs; using fresh evidence only.")
    except Exception as exc:
        logger.warning("Prior-research reuse check failed: %s", exc)
    entities_found: list = []

    # Deterministic historical gate + stats BEFORE the LLM narrates, so the
    # model can only narrate validated numbers (specs/11 S2) and the prompt
    # carries an explicit BLOCKED/PASSED verdict.
    gate: Dict[str, Any] = {}
    try:
        gate = call_history_gate(
            plan_text,
            market_data=market_data,
            price_history=price_history,
            financial_history=financial_history,
            fundamentals=fundamentals,
        )
    except Exception as exc:
        # Fail-closed: gate unavailable -> BLOCK comparison/chart.
        logger.warning("Historical gate failed: blocking comparison: %s", exc)
        gate = _fail_closed_gate(plan_text, f"historical validation unavailable ({exc})")
        thinking.append("Historical validation unavailable; comparison blocked.")
    # What-if intent never consumes market-history stats (no inheritance):
    # scenario math comes from deterministic what-if computation only.
    if gate.get("comparison_stats") and not is_what_if_query(plan_text):
        computed_numbers = {
            **(computed_numbers or {}),
            "comparison_stats": gate["comparison_stats"],
        }
    elif gate.get("comparison_stats") and is_what_if_query(plan_text):
        thinking.append("What-if query: historical stats withheld (scenario math only).")
    if gate.get("applies") and gate.get("blocked"):
        thinking.append(f"Historical gate BLOCKED: {(gate.get('blocked_reason', '') or '')[:160]}")
    if gate.get("partial"):
        thinking.append(f"Partial evidence: {gate.get('exclusion_note', '')[:160]}")

    # Canonical query semantics: plan_text (the merged clarification-aware
    # research intent) is the single source of truth for research AND
    # narration. The fragmentary user_query is kept only for display/trace.
    # Using the fragment for narration while the gate used the merged plan
    # let period/metric diverge (e.g. gate PASSED 3Y while the prompt asked
    # 1Y). Every stage below reads plan_text.
    try:
        await emit("judging")
        decision = await judge_sufficiency(
            user_query=plan_text,
            source_scope=source_scope,
            evidence=_evidence_inventory(
                rows,
                computed_numbers,
                news_context,
                market_data,
                fundamentals=fundamentals,
                macro_data=macro_data,
                price_history=price_history,
                financial_history=financial_history,
            ),
            prior_clarification=prior_clarification,
            prior_data=prior_data,
        )
        # An explicitly requested shape in the canonical query wins over the judge's.
        detected = detect_preferred_visual(plan_text)
        if detected is not None:
            decision.preferred_visual = detected
        # Phase 3 contract: publicly researchable information must never
        # require the user to supply it. When the query is a researchable
        # comparison (arbitrary entities + metrics detected), clarification
        # is DETERMINISTICALLY forbidden -- the judge's "clarify" is
        # overridden here in code, not by prompt compliance. Genuine
        # ambiguity (no entities/metrics) may still clarify. Generic
        # discipline: never ask for already-supplied or safely defaultable
        # info (timeframes, geographies, currencies, source prefs).
        try:
            if decision.decision == "clarify" and must_not_clarify is not None:
                if must_not_clarify(plan_text):
                    logger.warning(
                        "Deterministic clarification ban: researchable "
                        "comparison must be answered from evidence, not asked."
                    )
                    thinking.append(
                        "Clarification forbidden by deterministic rule "
                        "(researchable public-company comparison); "
                        "answering from researched evidence."
                    )
                    decision = Decision(
                        decision="answer",
                        missing="",
                        chart_from_prior=decision.chart_from_prior,
                        visual_plan=decision.visual_plan,
                        suggested_options=[],
                        preferred_visual=decision.preferred_visual,
                        tools_needed=decision.tools_needed,
                    )
                elif decision.missing:
                    try:
                        from app.services.data.comparison import (
                            clarification_is_redundant as _redundant,
                        )

                        redundant, why = _redundant(decision.missing, plan_text)
                    except Exception:
                        redundant, why = False, ""
                    if redundant:
                        logger.warning(
                            "Redundant clarification suppressed (%s); answering.", why
                        )
                        thinking.append(
                            f"Redundant clarification suppressed ({why}); answering."
                        )
                        decision = Decision(
                            decision="answer",
                            missing="",
                            chart_from_prior=decision.chart_from_prior,
                            visual_plan=decision.visual_plan,
                            suggested_options=[],
                            preferred_visual=decision.preferred_visual,
                            tools_needed=decision.tools_needed,
                        )
        except Exception as exc:
            logger.warning("Clarification-ban check failed: %s", exc)
        logger.info(
            f"Pipeline decision: {decision.decision} "
            f"(chart_from_prior={decision.chart_from_prior}, "
            f"preferred={decision.preferred_visual}, "
            f"plan={[item.kind for item in decision.visual_plan]})"
        )
        thinking.append(
            f"Judged: {decision.decision} "
            f"({len(rows)} row(s), {len(news_context)} snippet(s), "
            f"{len(market_data)} market series, "
            f"{len(price_history)} price-history, "
            f"{len(financial_history)} financial-history)"
        )

        narrate_rows = rows
        # Follow-up staleness: prior rows are reused ONLY when the current
        # request matches the prior entities/metrics/period. A changed
        # entity/metric/period or a what-if query regenerates from current
        # evidence, never stale data. A pure presentation change
        # (qualitative -> chart, "chart that") explicitly reuses prior rows
        # and regenerates visuals -- intent change alone is not stale there.
        _prior_query = str((prior_data or {}).get("from_query", "") or "")
        try:
            # Canonical staleness (P0#19): the single is_visual_stale_for_query
            # verdict drives prior-data reuse -- no divergent inline copy.
            # It covers entity/metric/period/what-if/intent; frequency/unit/
            # currency/source-identity changes additionally force staleness
            # below via validated-state comparison where available.
            _stale, _why = is_visual_stale_for_query(
                VisualOutput(visual_type="status", props={}, title="stale-probe"),
                plan_text, _prior_query,
            ) if _prior_query else (False, "")
            # is_visual_stale_for_query treats a pure chart presentation
            # follow-up ("chart that") as NOT stale -- honor that here.
            # Its probe visual carries no entities so only structural
            # query comparison applies (exactly what prior reuse needs).
        except Exception:
            _stale, _why = False, ""
        _what_if_now = is_what_if_query(plan_text)
        if decision.chart_from_prior and prior_data and not rows:
            # Follow-up on the previous answer ("chart that"): narrate from
            # the prior rows so the request resolves instead of clarifying --
            # unless stale (regenerate) or what-if (never reuse history).
            if _stale or _what_if_now:
                logger.info(
                    "Prior-data reuse blocked (stale=%s, what_if=%s: %s); "
                    "regenerating from current evidence.", _stale, _what_if_now, _why,
                )
                thinking.append("Prior data stale for this follow-up; using current evidence.")
                narrate_rows = rows
            else:
                narrate_rows = prior_data.get("rows", []) or []
        if not narrate_rows and prior_data:
            # Deterministic backstop (no judge needed): an explicit chart
            # request with no fresh rows but a prior answer's rows always
            # resolves from the prior rows -- unless stale/what-if.
            if re.search(CHART_INTENT_RE, plan_text, re.IGNORECASE) and not _stale and not _what_if_now:
                logger.info("Chart follow-up resolved from prior answer rows.")
                narrate_rows = prior_data.get("rows", []) or []

        await emit("narrating")
        output = await _narrate(
            user_query=plan_text,
            db_data=narrate_rows,
            computed_numbers=computed_numbers,
            news_context=news_context,
            source_scope=source_scope,
            company_name=company_name,
            decision=decision,
            prior_clarification=prior_clarification,
            prior_data=prior_data,
            market_data=market_data,
            forbid_clarify=False,
            web_sources=web_sources,
            fundamentals=fundamentals,
            macro_data=macro_data,
            macro_note=macro_note,
            price_history=price_history,
            financial_history=financial_history,
            comparison_gate=gate,
        )

        if (
            output.clarification is not None
            and prior_clarification
            and _same_question(
                output.clarification.question, prior_clarification
            )
        ):
            # Anti-loop backstop: the same question twice is forbidden, so
            # answer best-effort with assumptions instead of re-asking.
            logger.warning("Repeat clarification blocked; answering best-effort.")
            thinking.append("Repeat question blocked; answered best-effort.")
            output = await _narrate(
                user_query=plan_text,
                db_data=narrate_rows,
                computed_numbers=computed_numbers,
                news_context=news_context,
                source_scope=source_scope,
                company_name=company_name,
                decision=decision,
                prior_clarification=prior_clarification,
                prior_data=prior_data,
                market_data=market_data,
                forbid_clarify=True,
                web_sources=web_sources,
                fundamentals=fundamentals,
                macro_data=macro_data,
                macro_note=macro_note,
                price_history=price_history,
                financial_history=financial_history,
                comparison_gate=gate,
            )

        if output.clarification is not None:
            # Hard guard against the ask-user-for-data loop (seen live:
            # "Please provide annual revenue, net income, and profit margin
            # figures..."): public company statistics are researchable, so
            # a clarification requesting them is discarded and the pipeline
            # answers from researched evidence (or states what is missing)
            # instead of stalling or restarting on the user's reply.
            # Widened (Phase 3): ANY clarification on a deterministically
            # researchable comparison is suppressed, not just ones matching
            # the ask-for-data pattern -- the narrator must not re-open a
            # question the deterministic layer already closed.
            try:
                asks_data = (
                    clarification_asks_for_researchable_data(
                        output.clarification.question, plan_text
                    )
                    if clarification_asks_for_researchable_data is not None
                    else False
                )
            except Exception:
                asks_data = False
            try:
                banned = bool(
                    must_not_clarify is not None and must_not_clarify(plan_text)
                )
            except Exception:
                banned = False
            try:
                from app.services.data.comparison import (
                    clarification_is_redundant as _redundant2,
                )

                redundant2, _why2 = _redundant2(
                    output.clarification.question, plan_text
                )
            except Exception:
                redundant2, _why2 = False, ""
            if asks_data or banned or redundant2:
                logger.warning(
                    "Researchable-data clarification suppressed; "
                    "answering from researched evidence."
                )
                thinking.append(
                    "Suppressed ask-user-for-data clarification; "
                    "answered from researched evidence."
                )
                output = await _narrate(
                    user_query=plan_text,
                    db_data=narrate_rows,
                    computed_numbers=computed_numbers,
                    news_context=news_context,
                    source_scope=source_scope,
                    company_name=company_name,
                    decision=decision,
                    prior_clarification=prior_clarification,
                    prior_data=prior_data,
                    market_data=market_data,
                    forbid_clarify=True,
                    web_sources=web_sources,
                    fundamentals=fundamentals,
                    macro_data=macro_data,
                    macro_note=macro_note,
                    price_history=price_history,
                    financial_history=financial_history,
                    comparison_gate=gate,
                )

        if output.clarification is None and news_context:
            # Strip citation markers that point at no listed source.
            output.answer = sanitize_citations(output.answer, len(news_context))

        if output.clarification is not None and not output.clarification.options:
            # Both affordances, always: pills to tap AND a box to type. When
            # the narrator leaves options empty, backfill from the judge's
            # evidence-grounded suggestions; genuinely open-ended questions
            # keep [] and render type-only.
            if decision.suggested_options:
                output.clarification.options = list(decision.suggested_options[:4])
                logger.info("Backfilled clarification options from judge suggestions.")

        # Always bound, regardless of clarify-vs-answer: the deterministic
        # visual-plan reconciliation and narration-contract grounding below
        # (after this if/else) run unconditionally, but the real
        # build_validated_evidence_state() call only happens in the
        # "answer" branch below. Without this default, a clarify decision
        # left validated_state unbound and crashed both of those steps with
        # UnboundLocalError on every single clarification turn (caught by
        # their own try/except, so it degraded silently instead of
        # surfacing) -- same safe shape as the except-fallback further down,
        # so nothing downstream needs to change.
        validated_state: Dict[str, Any] = {
            "validated_entities": [], "validated_metrics": [],
            "sufficient": False, "partial": False, "blocked": bool(gate.get("blocked")),
            "comparison_stats": gate.get("comparison_stats"),
            "exclusion_note": str(gate.get("exclusion_note", "") or ""),
        }
        if output.clarification is not None:
            thinking.append(
                f"Clarifying (one question): {output.clarification.question[:120]}"
            )
        else:
            # Phase 13 contract (hard gate, enforced in CODE, never by
            # prompt): when the historical gate BLOCKED, the narrator must
            # not ship comparison visuals. Strip any LLM-fabricated graph /
            # comparison cards here -- grounding alone cannot catch a chart
            # whose numbers happen to appear in the snippets but whose
            # comparison the gate rejected.
            try:
                if gate.get("applies") and gate.get("blocked") and output.visuals:
                    before = len(output.visuals)
                    output.visuals = [
                        visual for visual in output.visuals
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                    stripped = before - len(output.visuals)
                    if stripped:
                        logger.info(
                            "Stripped %d fabricated comparison visual(s): gate BLOCKED.",
                            stripped,
                        )
                        thinking.append(
                            f"Stripped {stripped} comparison visual(s): "
                            "historical gate BLOCKED."
                        )
                # Fail-closed: zero confidence never ships a chart, even when
                # the gate passed (e.g. validator threw mid-flight and the
                # gate defaulted). Tables/sources prose visuals survive.
                try:
                    zero_conf = float(output.confidence or 0.0) <= 0.0
                except (TypeError, ValueError):
                    zero_conf = True
                if zero_conf and output.visuals:
                    before = len(output.visuals)
                    output.visuals = [
                        visual for visual in output.visuals
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                    stripped = before - len(output.visuals)
                    if stripped:
                        logger.info(
                            "Stripped %d chart visual(s): zero confidence.",
                            stripped,
                        )
                        thinking.append(
                            f"Stripped {stripped} chart visual(s): zero confidence."
                        )
            except Exception as exc:
                # Fail-closed: stripping itself failed -> drop all chart
                # visuals rather than risk shipping an unverified one.
                logger.warning("Visual strip failed, dropping charts: %s", exc)
                try:
                    output.visuals = [
                        visual for visual in (output.visuals or [])
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                except Exception:
                    output.visuals = []
            # Checked trust (P0#13 uniform): LLM-proposed numeric visuals
            # must ground in validated evidence/computation for EVERY visual
            # type and channel -- no row-presence exemption. Ungrounded ones
            # fall back to the deterministic figures path via ensure_visuals.
            if output.visuals and (
                news_context or narrate_rows or price_history
                or financial_history or market_data or computed_numbers
            ):
                kept, dropped = drop_ungrounded_visuals_evidence(
                    list(output.visuals), snippets=news_context,
                    rows=narrate_rows, price_history=price_history,
                    financial_history=financial_history,
                    market_data=market_data,
                    computed_numbers=computed_numbers,
                )
                if dropped:
                    thinking.append(
                        f"Dropped {dropped} ungrounded visual(s); "
                        "deterministic fallback applies."
                    )
                    output.visuals = kept
            # Decision.tools_needed is advisory post-research, but it is no
            # longer dead: tools the judge asked for with no evidence on hand
            # cap confidence and are recorded instead of silently ignored.
            try:
                if reconcile_judge_tools is not None and decision.tools_needed:
                    reconciled = reconcile_judge_tools(
                        decision.tools_needed,
                        _evidence_inventory(
                            narrate_rows, computed_numbers, news_context,
                            market_data, fundamentals=fundamentals,
                            macro_data=macro_data, price_history=price_history,
                            financial_history=financial_history,
                        ),
                    )
                    if reconciled.get("missing"):
                        logger.info(
                            "Judge-requested tools lacking evidence: %s",
                            reconciled["missing"],
                        )
                        thinking.append(
                            "Judge-requested tools lacking evidence: "
                            + ", ".join(reconciled["missing"])
                        )
                        output.confidence = min(float(output.confidence or 0.0), 0.65)
            except Exception as exc:
                logger.warning("Judge-tools reconcile failed: %s", exc)
            plan = [item.kind for item in decision.visual_plan]
            thinking.append(
                f"Answered from {len(narrate_rows)} row(s) + "
                f"{len(news_context)} snippet(s)"
                + (f"; planned visuals: {', '.join(plan)}" if plan else "")
            )
            providers = {
                str(source.get("provider", "")).strip().lower()
                for source in (web_sources or [])
                if str(source.get("provider", "")).strip()
            }
            # Single validated-evidence state: answer scope, stats,
            # confidence, visuals, and narration all read the same canonical
            # validated subset (never independent existence checks).
            try:
                _completeness_for_state = None
                if has_research_completeness() and plan_dict:
                    try:
                        _completeness_for_state = call_research_completeness(
                            plan_dict,
                            {
                                "price_history": price_history,
                                "financial_history": financial_history,
                                "market_data": market_data,
                                "fundamentals": fundamentals,
                                "snippets": news_context,
                            },
                        ) or {}
                    except Exception:
                        _completeness_for_state = None
                validated_state = build_validated_evidence_state(
                    query=plan_text, plan=plan_dict, gate=gate,
                    completeness=_completeness_for_state,
                )
            except Exception as exc:
                logger.warning("Validated-evidence state failed: %s", exc)
                validated_state = {
                    "validated_entities": [], "validated_metrics": [],
                    "sufficient": False, "partial": False, "blocked": bool(gate.get("blocked")),
                    "comparison_stats": gate.get("comparison_stats"),
                    "exclusion_note": str(gate.get("exclusion_note", "") or ""),
                }
            # Partial exclusion transparency (P0#5): state excluded
            # entities/metrics in prose exactly once per response, tracked
            # structurally (exclusion_disclosed flag), never by fragile
            # exact-string matching as the source of truth.
            try:
                from app.services.data.canonical import (
                    exclusion_note_for as _excl_for,
                )

                _excl_note = str(validated_state.get("exclusion_note", "") or "")
                if not _excl_note and validated_state.get("partial"):
                    _excl_note = _excl_for(validated_state)
                # What-if answers are scenario results, not comparisons: a
                # computed scenario suppresses comparison-metric exclusion
                # notes (the assumption + projected result are the evidence).
                try:
                    if is_what_if_query(plan_text) and (computed_numbers or {}).get("what_if"):
                        _excl_note = ""
                except Exception:
                    pass
                if _excl_note and output.clarification is None and not validated_state.get(
                    "exclusion_disclosed"
                ):
                    output.answer = str(output.answer or "").rstrip() + "\n\n" + _excl_note
                    try:
                        validated_state["exclusion_disclosed"] = True
                        validated_state["exclusion_note"] = _excl_note
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Exclusion-note append failed: %s", exc)
            # Explicit evidence requirements (P0#3): entities x metrics x
            # timeframe x data type, derived BEFORE reading retrieval results;
            # fulfilled/missing recorded in the trace (never re-requested).
            try:
                from app.services.data.canonical import (
                    derive_evidence_requirements as _derive_reqs,
                    reconcile_requirements as _reconcile_reqs,
                )

                _reqs = _derive_reqs(
                    list(plan_dict.get("entities", []) or []),
                    list(plan_dict.get("metrics", []) or []),
                    plan_dict.get("period_label"),
                )
                _per_ok: dict = {}
                try:
                    _per_ok = (_completeness_for_state or {}).get("per_entity", {}) or {}
                except Exception:
                    _per_ok = {}
                _rec = _reconcile_reqs(_reqs, _per_ok)
                if _rec.get("missing"):
                    thinking.append(
                        "Evidence requirements missing: "
                        + "; ".join(
                            f"{m['entity']}/{m['metric']}" for m in _rec["missing"][:6]
                        )
                    )
            except Exception as exc:
                logger.warning("Requirement reconcile failed: %s", exc)
            # Source-quality signal (P1#29): provider diversity + structured
            # provenance tiers recorded (authoritative filings/structured >
            # snippets). Count alone never decides quality.
            try:
                _provs = sorted(providers or [])
                if _provs:
                    thinking.append(f"Evidence providers: {', '.join(_provs[:4])}")
            except Exception:
                pass
            # Phase 16 contract: confidence is evidence-driven. The canonical
            # validated state produces a deterministic cap (BLOCKED -> 0,
            # insufficient -> 0, partial -> capped low); the LLM's subjective
            # number survives below it, never above.
            entities_found = sorted(set(
                list(validated_state.get("validated_entities", []) or [])
                or list(gate.get("validated_entities", []) or [])
            ))
            # Fall back to raw-found only when no validated subset exists
            # (non-comparison qualitative answers).
            if not entities_found and not gate.get("applies"):
                entities_found = sorted({
                    str(item.get("entity", "") or item.get("symbol", ""))
                    for lst in (list(price_history) + list(financial_history) + list(market_data))
                    for item in [lst if isinstance(lst, dict) else {}]
                    if isinstance(lst, dict) and (lst.get("values") or (lst.get("revenue", {}) or {}).get("values"))
                })
            metrics_found = list(
                validated_state.get("validated_metrics", [])
                or gate.get("validated_metrics", [])
                or (list(plan_dict.get("metrics", []) or []) if gate.get("applies") and not gate.get("blocked") else [])
            )
            try:
                if evidence_driven_confidence is not None and (plan_dict.get("is_comparison") or gate.get("applies")):
                    capped = evidence_driven_confidence(
                        output.confidence,
                        plan=plan_dict,
                        entities_found=entities_found,
                        metrics_found=metrics_found,
                        historical_ok=bool(gate.get("historical_ok", True)),
                        comparison_ok=bool(gate.get("comparison_ok", True)),
                        row_count=len(narrate_rows),
                        source_count=len(web_sources or []),
                        snippet_count=len(news_context),
                        provider_count=len(providers),
                        has_structured=bool(market_data or macro_data or fundamentals or price_history or financial_history),
                    )
                else:
                    capped = evidence_confidence_cap(
                        output.confidence,
                        row_count=len(narrate_rows),
                        snippet_count=len(news_context),
                        provider_count=len(providers),
                        has_market=bool(market_data or macro_data or fundamentals),
                    )
            except Exception as exc:
                logger.warning("Evidence-driven confidence failed: %s", exc)
                capped = evidence_confidence_cap(
                    output.confidence,
                    row_count=len(narrate_rows),
                    snippet_count=len(news_context),
                    provider_count=len(providers),
                    has_market=bool(market_data or macro_data or fundamentals),
                )
            # Historical gate overrides the generic cap: BLOCKED means 0.0
            # (insufficient evidence), PASSED keeps the generic cap. Partial
            # caps at 0.65 (never full 0.85). A short-term-only series must
            # never inflate confidence for a multi-year ask.
            if gate.get("applies"):
                if gate.get("blocked"):
                    if output.confidence != 0.0:
                        logger.info(
                            "Confidence forced to 0.0 by historical gate: %s",
                            (gate.get("blocked_reason", "") or "")[:160],
                        )
                        thinking.append("Confidence forced to 0.0: historical evidence insufficient.")
                    output.confidence = 0.0
                    capped = 0.0
                elif gate.get("partial"):
                    capped = min(capped, 0.65)
                    if output.confidence > capped + 0.005:
                        thinking.append(
                            f"Confidence capped by partial evidence: {output.confidence} -> {capped}"
                        )
                        output.confidence = capped
                else:
                    # PASSED gate keeps the evidence-driven cap as-is (P0#24):
                    # confidence represents evidence quality/coverage, and no
                    # PASSED verdict may lift it regardless of quality. A
                    # sparse two-point series earns its thin-evidence cap, not
                    # an automatic >=0.65.
                    pass
            # Sparse-series penalty (P0#24/P1#29): a validated gate over thin
            # observations (2 points for a multi-year ask) or a single
            # provider caps below full confidence; source count alone never
            # makes weak evidence look strong.
            try:
                _years = gate.get("years") if gate.get("applies") else None
                if _years and int(_years) >= 3 and not gate.get("blocked"):
                    _min_obs = 10**9
                    for _lst in (list(price_history or []) + list(market_data or [])):
                        if isinstance(_lst, dict) and _lst.get("values"):
                            _min_obs = min(_min_obs, len(list(_lst.get("values") or [])))
                    for _item in list(financial_history or []):
                        if isinstance(_item, dict):
                            for _bk in ("revenue", "net_income"):
                                _chunk = (_item.get(_bk, {}) or {})
                                if isinstance(_chunk, dict) and _chunk.get("values"):
                                    _min_obs = min(_min_obs, len(list(_chunk.get("values") or [])))
                    if _min_obs <= 2:
                        capped = min(capped, 0.45)
                        thinking.append(
                            "Confidence capped by sparse observations "
                            f"(min_obs={_min_obs} for {_years}Y)."
                        )
                    if len(providers) <= 1 and not (price_history or financial_history):
                        capped = min(capped, 0.65)
            except Exception:
                pass
            if capped < output.confidence - 0.005:
                logger.info(
                    f"Confidence capped by evidence: {output.confidence} -> {capped}."
                )
                thinking.append(
                    f"Confidence capped by evidence: {output.confidence} -> {capped}"
                )
                output.confidence = capped

        await emit("visuals")
        output = ensure_visuals(
            output,
            rows=narrate_rows,
            computed_numbers=computed_numbers,
            market_data=market_data,
            web_sources=web_sources,
            news_context=news_context,
            preferred_visual=decision.preferred_visual,
            query=plan_text,
            fundamentals=fundamentals,
            macro_data=macro_data,
            price_history=price_history,
            financial_history=financial_history,
        )
        if output.visuals:
            thinking.append(
                f"Visuals out: {', '.join(v.visual_type for v in output.visuals)}"
            )
        # Deterministic visual-plan reconciliation (P0#18): the judge's
        # visual_plan is advisory; the deterministic planner below is
        # authoritative. Divergence is logged (never silent) and the
        # deterministic verdict wins -- there is exactly one effective plan.
        try:
            _det_plan = plan_visuals_from_evidence(
                query=plan_text,
                validated_state=validated_state,
                has_rows=bool(narrate_rows),
                has_history=bool(price_history or financial_history),
                has_market=bool(market_data),
                has_snippets=bool(news_context),
            )
            _judge_plan = [item.kind for item in (decision.visual_plan or [])]
            if set(_judge_plan) != set(_det_plan):
                logger.info(
                    "Visual plan reconciled: judge=%s deterministic=%s.",
                    _judge_plan, _det_plan,
                )
                thinking.append(
                    f"Visual plan reconciled (deterministic wins): {', '.join(_det_plan) or 'none'}"
                )
        except Exception as exc:
            logger.warning("Visual-plan reconcile failed: %s", exc)
        # Code-grounded narration + final validation (P0#22/#23): claims
        # exceeding validated evidence are removed/blocked in code, never by
        # prompt compliance alone. Follow-ups are grounded (P1#30) and the
        # what-if assumption is verified verbatim (P0#10).
        try:
            output = apply_narration_contract(
                output,
                validated_state=validated_state,
                computed_numbers=computed_numbers,
                gate=gate,
                thinking=thinking,
            )
        except Exception as exc:
            logger.warning("Narration contract failed (fail-open prose kept): %s", exc)
        # Phase 18: compact structured research state for follow-ups (never
        # raw payloads). Phase 19: structured trace log (no secrets/PII).
        try:
            output.research_state = {
                "query": user_query[:300],
                "plan": {
                    "entities": list(plan_dict.get("entities", []) or []),
                    "metrics": list(plan_dict.get("metrics", []) or []),
                    "time_range": plan_dict.get("time_range"),
                    "requires_history": bool(plan_dict.get("requires_history")),
                    "required_tools": list(plan_dict.get("required_tools", []) or []),
                },
                "entities_found": entities_found if output.clarification is None else [],
                "gate": {
                    "applies": bool(gate.get("applies")),
                    "blocked": bool(gate.get("blocked")),
                    "blocked_reason": str(gate.get("blocked_reason", "") or "")[:300],
                },
                "confidence": output.confidence,
                "sources": [
                    {
                        "title": str(source.get("title", ""))[:120],
                        "url": str(source.get("url", ""))[:300],
                        "provider": str(source.get("provider", ""))[:60],
                    }
                    for source in (web_sources or [])[:12]
                ],
            }
        except Exception as exc:
            logger.warning("Research-state build failed: %s", exc)
        try:
            _executed = [
                        name for name, lst in (
                            ("market", market_data), ("macro", macro_data),
                            ("fundamentals", fundamentals),
                            ("price_history", price_history),
                            ("financial_history", financial_history),
                        ) if lst
                    ] + (["snippets"] if news_context else [])
            _completeness: Dict[str, Any] = {}
            _missing_entities: list = []
            _missing_metrics: list = []
            try:
                if has_research_completeness() and plan_dict:
                    _completeness = call_research_completeness(
                        plan_dict,
                        {
                            "price_history": price_history,
                            "financial_history": financial_history,
                            "market_data": market_data,
                            "fundamentals": fundamentals,
                            "snippets": news_context,
                        },
                    ) or {}
                    _missing_entities = list(_completeness.get("missing_entities", []) or [])
                    _missing_metrics = list(_completeness.get("missing_metrics", []) or [])
            except Exception as exc:
                logger.warning("Completeness for trace failed: %s", exc)
            trace = (
                build_trace(
                    query=user_query,
                    plan=plan_dict,
                    tools_requested=list(plan_dict.get("required_tools", []) or []),
                    tools_executed=_executed,
                    planned_tools=list(getattr(decision, "tools_needed", []) or []),
                    actually_executed_tools=_executed,
                    tool_results={
                        "rows": len(narrate_rows),
                        "snippets": len(news_context),
                        "market_series": len(market_data or []),
                        "price_history": len(price_history or []),
                        "financial_history": len(financial_history or []),
                    },
                    missing_entities=_missing_entities,
                    missing_metrics=_missing_metrics,
                    completeness={
                        "comparison_complete": bool(_completeness.get("comparison_complete"))
                        if _completeness else (not bool(gate.get("blocked")) if gate.get("applies") else True),
                        "missing": list((_completeness.get("missing") or [])[:6]) if _completeness else [],
                    },
                    missing_evidence=(
                        [gate.get("blocked_reason", "")[:200]]
                        if gate.get("blocked") else []
                    ),
                    comparison_gate=gate,
                    calculated_stats=bool((computed_numbers or {}).get("comparison_stats")),
                    visual_decision=[v.visual_type for v in (output.visuals or [])],
                    final_confidence=output.confidence,
                    entities=list(plan_dict.get("entities", []) or []),
                    metrics=list(plan_dict.get("metrics", []) or []),
                    time_range=plan_dict.get("time_range"),
                )
                if build_trace is not None else {}
            )
            log_runtime_trace(trace)
        except Exception as exc:
            logger.warning("Trace log failed: %s", exc)
        output.thinking = thinking + list(output.thinking or [])
        return output

    except ValidationError as ve:
        logger.error(f"Schema validation failed: {ve}")
        return await _rescue_or_fallback(
            reason="Sorry, I could not process your request properly. Please try again."
        )

    except ValueError as ve:
        logger.error(f"JSON extraction failed: {ve}")
        return await _rescue_or_fallback(
            reason="I had trouble understanding the data. Please rephrase your query."
        )

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        return await _rescue_or_fallback(
            reason="Something went wrong. Please try again."
        )


def _extract_answer_prefix(partial_json: str) -> str:
    """Best-effort current value of the top-level `"answer"` string in partial JSON.

    Streaming narration accumulates raw JSON text chunk by chunk; this pulls
    out whatever of the `answer` prose has arrived so far so it can render
    live. Handles JSON string escapes (`\"`, `\\`, `\n`, `\uXXXX`, …) and an
    unterminated trailing literal (stops at end-of-input). Returns `""` when
    no `"answer"` string has started yet. Pure, never raises.
    """
    try:
        match = re.search(r'"answer"\s*:\s*"', partial_json)
        if not match:
            return ""
        text = partial_json[match.end():]
        out: list[str] = []
        i = 0
        _SIMPLE_ESCAPES = {
            '"': '"', "\\": "\\", "/": "/",
            "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
        }
        while i < len(text):
            char = text[i]
            if char == '"':
                break  # terminated string value
            if char != "\\":
                out.append(char)
                i += 1
                continue
            # Escape sequence: need at least one more char.
            if i + 1 >= len(text):
                break  # incomplete trailing backslash
            nxt = text[i + 1]
            if nxt in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[nxt])
                i += 2
            elif nxt == "u":
                hex_part = text[i + 2:i + 6]
                if len(hex_part) < 4 or not all(
                    c in "0123456789abcdefABCDEF" for c in hex_part
                ):
                    break  # incomplete \u escape
                out.append(chr(int(hex_part, 16)))
                i += 6
            else:
                # Unknown escape: keep the char literally, stay in sync.
                out.append(nxt)
                i += 2
        return "".join(out)
    except Exception:
        return ""


async def _narrate_streaming(
    prompt: str,
    system_prompt: str,
    model: Optional[str],
    on_token: Callable[[str], Awaitable[None]],
) -> str:
    """Stream narration deltas, forwarding growing `answer` prose to `on_token`.

    Returns the complete accumulated text for the normal parse + validate
    path. A failing token callback is logged and skipped (never breaks the
    narration); an empty/failed stream raises so the caller falls back to
    the non-streamed call.
    """
    accumulated: list[str] = []
    emitted = ""
    async for delta in call_llm_stream(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=0.2,
        max_tokens=2000,
    ):
        if not delta:
            continue
        accumulated.append(delta)
        current = _extract_answer_prefix("".join(accumulated))
        if len(current) > len(emitted):
            new_text = current[len(emitted):]
            emitted = current
            try:
                await on_token(new_text)
            except Exception as exc:
                logger.warning(f"Token callback failed: {exc}")
    full = "".join(accumulated).strip()
    if not full:
        raise RuntimeError("streaming narration returned no content")
    return full


async def _narrate(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: dict,
    news_context: list,
    source_scope: SOURCE_SCOPES,
    company_name: Optional[str],
    decision: Decision,
    prior_clarification: Optional[str],
    prior_data: Optional[Dict[str, Any]],
    market_data: list,
    forbid_clarify: bool,
    web_sources: Optional[list] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    macro_note: Optional[str] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    comparison_gate: Optional[Dict[str, Any]] = None,
) -> PipelineOutput:
    """One narration call: prompt -> LLM -> validated PipelineOutput.

    Live-web synthesis routes through the stronger model (specs/12): scraped
    prose is the highest-hallucination-risk evidence, so source_scope in
    ("live_web", "both") uses groq_strong_model while own-data keeps the
    default. Unconfigured strong model == default (no behavior change).
    """
    prompt = build_prompt(
        user_query,
        db_data,
        computed_numbers,
        news_context,
        source_scope,
        company_name,
        decision=decision,
        prior_clarification=prior_clarification,
        prior_data=prior_data,
        market_data=market_data,
        forbid_clarify=forbid_clarify,
        web_sources=web_sources,
        fundamentals=fundamentals,
        macro_data=macro_data,
        macro_note=macro_note,
        price_history=price_history,
        financial_history=financial_history,
        comparison_gate=comparison_gate,
    )

    try:
        narration_model: Optional[str] = None
        if source_scope in ("live_web", "both"):
            narration_model = get_settings().groq_strong_model
            logger.info("Live-web narration routed to stronger model.")
    except Exception:
        narration_model = None

    result = await call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        model=narration_model,
        temperature=0.2,
        max_tokens=2000,
        json_schema={"name": "pipeline_output", "schema": _PIPELINE_STRICT_SCHEMA},
    )

    raw_output = (result.get("content") or "").strip()
    if not raw_output:
        # Same transient-empty-completion rescue as the judge: one retry with
        # slightly higher temperature before giving up on this narration.
        logger.warning("Narration got empty content; retrying once.")
        result = await call_llm(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            model=narration_model,
            temperature=0.3,
            max_tokens=2000,
            json_schema={"name": "pipeline_output", "schema": _PIPELINE_STRICT_SCHEMA},
        )
        raw_output = (result.get("content") or "").strip()
    logger.info(f"LLM source used: {result.get('source', 'unknown')}")

    parsed = normalize_pipeline_payload(extract_json(raw_output))

    try:
        return PipelineOutput(**parsed)
    except ValidationError as ve:
        # Retry-with-clarification (one bounded repair, failure path only):
        # feed Pydantic's exact error back for one targeted fix. Success
        # returns the corrected output; any further failure re-raises so the
        # caller falls through to today's exact _rescue_or_fallback.
        logger.warning(f"Narration validation failed, attempting one repair: {ve}")
        repaired = await _attempt_validation_repair(
            original_prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            raw_output=raw_output,
            validation_error=str(ve),
            model=narration_model,
        )
        if repaired is not None:
            return repaired
        raise


async def _attempt_validation_repair(
    original_prompt: str,
    system_prompt: str,
    raw_output: str,
    validation_error: str,
    model: Optional[str] = None,
) -> Optional[PipelineOutput]:
    """One targeted repair for a narration payload that failed validation.

    Sends the original prompt plus the exact validation error back (not a
    blind re-ask), reusing the same json_schema settings as the original
    call. Bounded to a single call — never a loop. Returns the validated
    PipelineOutput on success, None on any further failure (caught, logged,
    never raised); the caller falls through to today's exact fallback either
    way. Happy-path call count is unchanged (this runs only on ValidationError).
    """
    try:
        repair_prompt = (
            f"{original_prompt}\n\nYour previous response failed validation: "
            f"{validation_error}. Return corrected JSON only, fixing exactly "
            "what's described above — do not change anything that wasn't flagged."
        )
        result = await call_llm(
            prompt=repair_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=0.2,
            max_tokens=2000,
            json_schema={"name": "pipeline_output", "schema": _PIPELINE_STRICT_SCHEMA},
        )
        repaired_raw = (result.get("content") or "").strip()
        if not repaired_raw:
            return None
        # Same parse + validate as the happy path — no duplicated logic.
        parsed = normalize_pipeline_payload(extract_json(repaired_raw))
        return PipelineOutput(**parsed)
    except Exception as exc:
        logger.warning(f"Validation repair attempt failed: {exc}")
        return None


async def _narrate_prose_rescue(
    user_query: str,
    rows: Sequence[dict],
    news_context: list,
    market_data: list,
) -> Optional[PipelineOutput]:
    """Last resort when structured narration fails: one plain-text call over
    the real evidence. Returns None when there is no evidence to speak from
    (then the honest generic fallback stands) or when the rescue also fails."""
    rows = list(rows or [])
    news_context = list(news_context or [])
    market_data = list(market_data or [])
    if not rows and not news_context and not market_data:
        return None
    evidence_parts = []
    if rows:
        evidence_parts.append(
            "Rows:\n"
            + "\n".join(json.dumps(row, default=str) for row in rows[:8])
        )
    if news_context:
        evidence_parts.append(
            "Web snippets:\n"
            + "\n".join(
                f"[{index}] {snippet}"
                for index, snippet in enumerate(news_context[:8], start=1)
            )
        )
    if market_data:
        evidence_parts.append(
            "Market series:\n"
            + "\n".join(
                f"{item.get('entity', 'series')}: "
                f"{list(item.get('values') or [])[-8:]}"
                for item in market_data[:4]
            )
        )
    try:
        result = await call_llm(
            prompt=(
                f"Question:\n{user_query}\n\nEvidence:\n" + "\n\n".join(evidence_parts)
            ),
            system_prompt=PROSE_RESCUE_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=600,
        )
        text = (result.get("content") or "").strip()
        if not text:
            return None
        if text.startswith("{") or text.startswith("["):
            # Not prose (a JSON blob or fragment) - refusing to present it
            # as an answer keeps the honest generic fallback.
            logger.warning("Prose rescue returned JSON, not prose; discarding.")
            return None
        return fallback_output(reason=text, confidence=0.35)
    except Exception as exc:
        logger.warning(f"Prose rescue failed: {exc}")
        return None


def evidence_confidence_cap(
    model_confidence: float,
    *,
    row_count: int,
    snippet_count: int,
    provider_count: int,
    has_market: bool,
) -> float:
    """Cap the model's confidence by what the evidence actually supports.

    A single-source comparative claim must never read "High": strong evidence
    (3+ rows/snippets, 2+ independent providers, market series) caps at 0.90,
    thin evidence at 0.65, and nothing at all at 0.35. The model's own number
    survives below the cap, so question-difficulty signal is kept.
    """
    if (
        row_count >= 3
        or snippet_count >= 3
        or provider_count >= 2
        or has_market
    ):
        cap = 0.90
    elif row_count >= 1 or snippet_count >= 1:
        cap = 0.65
    else:
        cap = 0.35
    try:
        return round(min(float(model_confidence), cap), 2)
    except (TypeError, ValueError):
        return cap


def _evidence_inventory(
    rows: Sequence[dict],
    computed_numbers: dict,
    news_context: list,
    market_data: list,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
) -> Dict[str, Any]:
    """Describe what the tools actually returned, for the judge's verdict."""
    rows = list(rows or [])
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    return {
        "row_count": len(rows),
        "columns": columns,
        "computed_stat_keys": sorted((computed_numbers or {}).keys()),
        "web_snippet_count": len(news_context or []),
        "market_entities": [
            str(item.get("entity", "series")) for item in (market_data or [])
        ],
        "market_is_short_term_only": bool(market_data),
        "price_history_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (price_history or [])
        ],
        "financial_history_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (financial_history or [])
        ],
        "fundamentals_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (fundamentals or [])
        ],
        "fundamentals_is_snapshot_only": bool(fundamentals),
        "macro_entities": [
            str(item.get("entity", "series")) for item in (macro_data or [])
        ],
    }

__all__ = [
    "_attempt_validation_repair",
    "_evidence_inventory",
    "_extract_answer_prefix",
    "_narrate",
    "_narrate_prose_rescue",
    "_narrate_streaming",
    "evidence_confidence_cap",
    "log_runtime_trace",
    "run_pipeline",
]
