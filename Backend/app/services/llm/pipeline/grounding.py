"""Number grounding, provenance, visual validation. Split from langchain_pipeline.py; behavior unchanged."""

import logging
import re

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .shared import call_numbers_grounded
from .models import PipelineOutput, VisualOutput
from .prompts import CHART_INTENT_RE
from .deps import build_research_plan, decompose_comparison_query
from .visuals import _is_number, _visual_intent

logger = logging.getLogger(__name__)



# --- Grounding: the LLM may propose comparison/graph visuals for web-only
# evidence; trust there is checked, not implicit. Every numeric value in the
# visual must appear in the cited snippets (formatting slop allowed) or the
# visual is discarded for the deterministic figures fallback. ---

_VISUAL_NUMBER_RE = re.compile(
    r"([$€₹£])?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand|%|percent)?"
)
_VISUAL_SCALE = {
    "k": 1e3, "K": 1e3, "thousand": 1e3,
    "M": 1e6, "million": 1e6,
    "B": 1e9, "billion": 1e9,
}


def _parse_scaled_number(text: str) -> list[float]:
    values: list[float] = []
    for match in _VISUAL_NUMBER_RE.finditer(text or ""):
        if not match.group(2):
            continue
        raw_digits = match.group(2).replace(",", "")
        # Bare 4-digit years (2024) are dates, not plottable data - skip them
        # only when there is no money/percent/scale marker attached.
        if not match.group(1) and not match.group(3):
            if raw_digits in ("2022", "2023", "2024", "2025", "2026", "2027"):
                continue
            if len(raw_digits) == 4 and raw_digits.startswith(("19", "20")):
                continue
        try:
            amount = float(raw_digits)
        except ValueError:
            continue
        scale = _VISUAL_SCALE.get(match.group(3) or "", 1)
        values.append(amount * scale)
    return values


def _iter_visual_numbers(props: Any) -> list[float]:
    """All plottable numbers in a visual's props: numeric leaves verbatim +
    scaled numbers parsed from money/percent-like strings. Label-only date
    strings contribute nothing (no money/percent marker, years skipped)."""
    found: list[float] = []
    stack = [props]
    while stack:
        node = stack.pop()
        if isinstance(node, bool):
            continue
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, str):
            # Only strings that look like figures ($, %, k/M/B) count;
            # plain category labels ("east", "Jan 01") are ignored.
            if re.search(r"[$€₹£%]", node) or re.search(
                r"\b(k|K|M|B|million|billion|thousand|percent)\b", node
            ):
                found.extend(_parse_scaled_number(node))
            elif re.fullmatch(r"\s*[\d,]+(\.\d+)?\s*", node):
                try:
                    found.append(float(node.replace(",", "").strip()))
                except ValueError:
                    pass
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def _visual_numbers_grounded(visual: VisualOutput, snippets: list) -> bool:
    """True when every number in the visual appears in the snippets.

    Both sides go through the same scaled parser ($300k == 300000), so
    formatting slop is allowed but invented magnitudes are not. Years are
    excluded on both sides (dates, not data). Tolerance is tight (1%) to
    absorb rounding, not to bless nearby-but-different figures.
    """
    numbers = _iter_visual_numbers(visual.props)
    if not numbers:
        return True  # qualitative cards (timeline, outlook) need no grounding
    snippet_values: list[float] = []
    for snippet in snippets or []:
        snippet_values.extend(_parse_scaled_number(str(snippet)))
    if not snippet_values:
        return False
    for value in numbers:
        grounded = any(
            abs(candidate - value)
            <= max(1e-6, abs(value) * 0.01, abs(candidate) * 0.01)
            for candidate in snippet_values
        )
        if not grounded:
            return False
    return True


def _evidence_numbers(
    snippets: Optional[list] = None,
    rows: Optional[Sequence[dict]] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    market_data: Optional[list] = None,
    computed_numbers: Optional[dict] = None,
) -> list[float]:
    """Every validated numeric value across all evidence channels (P0#13).

    Uniform grounding pool: a displayed number is traceable when it matches
    any validated evidence value or deterministic computation output --
    regardless of visual type or channel. Row presence never disables this.
    """
    pool: list[float] = []
    try:
        for snippet in snippets or []:
            pool.extend(_parse_scaled_number(str(snippet)))
    except Exception:
        pass
    try:
        for row in list(rows or [])[:500]:
            if not isinstance(row, dict):
                continue
            for value in row.values():
                if _is_number(value):
                    pool.append(float(value))
                elif isinstance(value, str):
                    pool.extend(_parse_scaled_number(value))
    except Exception:
        pass
    try:
        for lst in (list(price_history or []) + list(market_data or [])):
            if isinstance(lst, dict):
                for value in list(lst.get("values") or [])[:500]:
                    if _is_number(value):
                        pool.append(float(value))
        for item in list(financial_history or []):
            if not isinstance(item, dict):
                continue
            for block_key in ("revenue", "net_income"):
                block = (item.get(block_key, {}) or {})
                if isinstance(block, dict):
                    for value in list(block.get("values") or [])[:500]:
                        if _is_number(value):
                            pool.append(float(value))
    except Exception:
        pass
    try:
        stack = [computed_numbers or {}]
        while stack:
            node = stack.pop()
            if _is_number(node):
                pool.append(float(node))
            elif isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, (list, tuple)):
                stack.extend(node)
    except Exception:
        pass
    return pool


def _visual_numbers_grounded_in_pool(visual: VisualOutput, pool: list[float]) -> bool:
    """True when every number in the visual appears in the evidence pool."""
    numbers = _iter_visual_numbers(visual.props)
    if not numbers:
        return True  # qualitative cards need no grounding
    if not pool:
        return False
    for value in numbers:
        grounded = any(
            abs(candidate - value)
            <= max(1e-6, abs(value) * 0.01, abs(candidate) * 0.01)
            for candidate in pool
        )
        if not grounded:
            return False
    return True


def drop_ungrounded_visuals(
    visuals: list, snippets: list
) -> tuple[list, int]:
    """Split LLM-proposed visuals into (kept, dropped_count).

    Uniform grounding (P0#13): applies to EVERY visual type with numbers --
    no row-based exemption. Callers with structured evidence should prefer
    drop_ungrounded_visuals_evidence (full pool); this snippet variant is
    kept for web-only paths.
    """
    kept: list = []
    dropped = 0
    for visual in visuals or []:
        try:
            if call_numbers_grounded(visual, snippets):
                kept.append(visual)
            else:
                dropped += 1
                logger.info(
                    "Dropped ungrounded %s visual '%s'.",
                    getattr(visual, "visual_type", "?"),
                    getattr(visual, "title", "")[:60],
                )
        except Exception as exc:
            # Fail-closed: a grounding check that itself throws must drop
            # the visual, never keep an unverified number.
            logger.warning("Grounding check failed, dropping visual: %s", exc)
            dropped += 1
    return kept, dropped


def drop_ungrounded_visuals_evidence(
    visuals: list,
    *,
    snippets: Optional[list] = None,
    rows: Optional[Sequence[dict]] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    market_data: Optional[list] = None,
    computed_numbers: Optional[dict] = None,
) -> tuple[list, int]:
    """Uniform grounding across all channels and visual types (P0#13/P0#14).

    No exemptions for row-derived, table, or financial-history visuals: any
    displayed numeric value must be traceable to validated evidence or a
    deterministic computation. Fail-closed on checker exceptions.
    """
    pool = _evidence_numbers(
        snippets=snippets, rows=rows, price_history=price_history,
        financial_history=financial_history, market_data=market_data,
        computed_numbers=computed_numbers,
    )
    kept: list = []
    dropped = 0
    for visual in visuals or []:
        try:
            if _visual_numbers_grounded_in_pool(visual, pool):
                kept.append(visual)
            else:
                dropped += 1
                logger.info(
                    "Dropped ungrounded %s visual '%s' (evidence pool).",
                    getattr(visual, "visual_type", "?"),
                    getattr(visual, "title", "")[:60],
                )
        except Exception as exc:
            logger.warning("Grounding check failed, dropping visual: %s", exc)
            dropped += 1
    return kept, dropped


def build_visual_provenance(
    *,
    query: str,
    entities: Sequence[str],
    metric: Optional[str] = None,
    units: Optional[str] = None,
    timeframe: Optional[str] = None,
    frequency: Optional[str] = None,
    source_ids: Optional[Sequence[str]] = None,
    computation_ids: Optional[Sequence[str]] = None,
    data_points: Optional[Any] = None,
) -> Dict[str, Any]:
    """Provenance payload attached to every synthesized visual."""
    try:
        time_label = timeframe
        if time_label is None and decompose_comparison_query is not None:
            d = decompose_comparison_query(query or "") or {}
            time_label = d.get("period_label")
    except Exception:
        time_label = timeframe
    return {
        "intent": _visual_intent(query),
        "entities": list(entities or []),
        "metric": metric,
        "units": units,
        "timeframe": time_label,
        "frequency": frequency,
        "source_ids": list(source_ids or []),
        "computation_ids": list(computation_ids or []),
        "data_points": data_points,
    }


def attach_provenance(visual: VisualOutput, provenance: Dict[str, Any]) -> VisualOutput:
    """Attach provenance to a visual (mutates and returns it)."""
    try:
        visual.provenance = dict(provenance or {})
    except Exception:
        pass
    return visual


def _visual_entities(visual: VisualOutput) -> List[str]:
    """Entities a visual claims (datasets/groups/labels), lowercased."""
    out: List[str] = []
    try:
        props = visual.props or {}
        for ds in (props.get("datasets") or []):
            name = str((ds or {}).get("name", "") or "").strip()
            if name and name.lower() not in ("amount", "percent", "value", "series"):
                out.append(name)
        for grp in (props.get("groups") or []):
            label = str((grp or {}).get("label", "") or "").strip()
            # Group labels carry citations ("Acme ... [1]"); take head token.
            head = re.split(r"[\s\[\(,;]+", label)[0] if label else ""
            if head:
                out.append(label)
        # Table first-column entities: only explicit entity columns
        # ("Company"/"Entity") claim entities. Figure/source listings carry
        # verbatim cited text, not entity claims (their entities live in
        # provenance, not in the display column).
        if visual.visual_type == "table":
            cols = list(props.get("columns") or [])
            vals = list(props.get("values") or [])
            if cols and vals and cols[0].lower() in ("company", "entity"):
                for row in vals[:8]:
                    if row:
                        out.append(str(row[0])[:60])
    except Exception:
        pass
    return out


def validate_visual_provenance(
    visual: VisualOutput,
    *,
    query: str,
    validated_entities: Sequence[str],
    validated_metrics: Sequence[str],
    expected_timeframe: Optional[str] = None,
    allowed_intents: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
    """Validate one visual against the answer's validated evidence.

    Rejects when entity/metric/timeframe/units/source untraceable, values
    cannot be traced, or semantic intent differs. Fail-closed: any check
    exception rejects. Pure.
    """
    try:
        prov = getattr(visual, "provenance", None) or {}
        intent = str(prov.get("intent", "") or _visual_intent(query))
        expected_intent = _visual_intent(query)
        # What-if never reuses market-history intent and vice versa.
        if expected_intent == "what_if" and intent == "historical_comparison":
            return False, "stale intent: market-history chart for what-if query"
        if expected_intent == "historical_comparison" and intent == "what_if":
            return False, "stale intent: what-if visual for historical query"
        if allowed_intents and intent not in list(allowed_intents):
            return False, f"intent mismatch: {intent} not in {list(allowed_intents)}"
        # Entity must be within validated set (subset allowed for partial).
        try:
            want = {str(e).strip().lower() for e in (validated_entities or []) if str(e).strip()}
            if want:
                claimed = _visual_entities(visual)
                # Only enforce when the visual names concrete entities.
                named = [c for c in claimed if c and len(c) >= 2]
                if named:
                    for claim in named:
                        cl = claim.lower()
                        # A claim matches when it mentions a validated entity
                        # (labels carry citations/context, not bare names).
                        if not any(v in cl or cl in v for v in want):
                            # Explicit sources-only policy (P0#14): ONLY the
                            # sources/timeline/outlook qualitative tables are
                            # provenance-exempt (they list citations, not
                            # comparison entities). Data tables (figures,
                            # financial, results) obey the same entity
                            # contract as charts.
                            title = str(getattr(visual, "title", "") or "").lower()
                            if visual.visual_type == "table" and any(
                                k in title for k in ("source", "timeline", "outlook")
                            ):
                                continue
                            return False, f"entity mismatch: {claim!r} not in validated {sorted(want)}"
        except Exception as exc:
            return False, f"entity check unavailable ({exc})"
        # Metric must be within validated set when both state one.
        try:
            prov_metric = str(prov.get("metric", "") or "").strip().lower()
            want_m = {str(m).strip().lower() for m in (validated_metrics or []) if str(m).strip()}
            if prov_metric and want_m and prov_metric not in want_m:
                return False, f"metric mismatch: {prov_metric} not in {sorted(want_m)}"
        except Exception as exc:
            return False, f"metric check unavailable ({exc})"
        # Timeframe must match when both state one.
        try:
            prov_time = str(prov.get("timeframe", "") or "").strip().lower()
            exp_time = str(expected_timeframe or "").strip().lower()
            if prov_time and exp_time and prov_time != exp_time:
                return False, f"timeframe mismatch: {prov_time} vs {exp_time}"
        except Exception as exc:
            return False, f"timeframe check unavailable ({exc})"
        # Provenance must name a traceable source/computation. Tables obey
        # the same contract as charts (P0#14); only the explicit
        # qualitative allowlist (sources/timeline/outlook) is exempt.
        try:
            has_source = bool((prov.get("source_ids") or []))
            has_comp = bool((prov.get("computation_ids") or []))
            has_data = prov.get("data_points") is not None
            _title = str(getattr(visual, "title", "") or "").lower()
            _qualitative = visual.visual_type == "table" and any(
                k in _title for k in ("source", "timeline", "outlook")
            )
            if visual.visual_type in ("graph", "comparison", "table") and not _qualitative:
                if not (has_source or has_comp or has_data):
                    return False, "source untraceable: no source/computation IDs"
        except Exception as exc:
            return False, f"source check unavailable ({exc})"
        return True, "visual provenance valid"
    except Exception as exc:
        return False, f"provenance validation failed ({exc})"


def is_visual_stale_for_query(
    visual: VisualOutput,
    current_query: str,
    prior_query: Optional[str] = None,
) -> Tuple[bool, str]:
    """True when a visual from a prior query must not be reused.

    Stale when entity, metric, or period changed, or when what-if intent
    changed (different computation). A pure presentation change to a chart
    follow-up ("chart that") is NOT stale: visuals regenerate from prior
    rows. Generic across arbitrary entities/metrics/periods. Pure.
    """
    try:
        if not prior_query or not current_query:
            return False, ""
        if decompose_comparison_query is None:
            return False, ""
        cur = decompose_comparison_query(current_query or "") or {}
        prev = decompose_comparison_query(prior_query or "") or {}
        cur_e = {str(e).strip().lower() for e in (cur.get("entities", []) or [])}
        prev_e = {str(e).strip().lower() for e in (prev.get("entities", []) or [])}
        if cur_e and prev_e and cur_e != prev_e:
            return True, f"entity changed {sorted(prev_e)} -> {sorted(cur_e)}"
        cur_m = {str(m).strip().lower() for m in (cur.get("metrics", []) or [])}
        prev_m = {str(m).strip().lower() for m in (prev.get("metrics", []) or [])}
        if cur_m and prev_m and cur_m != prev_m:
            return True, f"metric changed {sorted(prev_m)} -> {sorted(cur_m)}"
        if (cur.get("period_label") or "") != (prev.get("period_label") or ""):
            if cur.get("period_label") or prev.get("period_label"):
                return True, f"period changed {prev.get('period_label')} -> {cur.get('period_label')}"
        cur_intent = _visual_intent(current_query)
        prev_intent = _visual_intent(prior_query)
        if (cur_intent == "what_if") != (prev_intent == "what_if"):
            return True, "what-if intent changed"
        # Chart presentation follow-ups regenerate from prior rows (not stale).
        if cur_intent != prev_intent:
            if re.search(CHART_INTENT_RE, current_query or "", re.IGNORECASE):
                return False, ""
            return True, "intent changed"
        return False, ""
    except Exception as exc:
        # Fail-closed: staleness check unavailable -> treat as stale.
        return True, f"staleness check unavailable ({exc})"


def _attach_history_provenance(
    visual: VisualOutput, query: str, evidence: list, years: Optional[int], metric: Optional[str]
) -> VisualOutput:
    """Attach validated-history provenance to a synthesized visual."""
    try:
        entities = [str(item.get("entity", item.get("symbol", ""))) for item in (evidence or []) if isinstance(item, dict)]
        entities = [e for e in entities if e][:4]
        units: Optional[str] = None
        freq: Optional[str] = None
        try:
            first = next((i for i in (evidence or []) if isinstance(i, dict)), {})
            units = str(first.get("currency", "") or "").strip() or None
            freq = str(first.get("frequency", "") or "").strip() or None
        except Exception:
            pass
        source_ids = [f"yahoo:{e}" for e in entities]
        attach_provenance(visual, build_visual_provenance(
            query=query, entities=entities, metric=metric, units=units,
            timeframe=f"{years}Y" if years else None, frequency=freq,
            source_ids=source_ids, computation_ids=["comparison_stats"] if metric else [],
            data_points={"entities": entities, "metric": metric},
        ))
    except Exception as exc:
        logger.warning("Provenance attach failed: %s", exc)
    return visual


def _provenance_filter_final(
    visuals: list, query: str, gate: Dict[str, Any]
) -> list:
    """Final provenance gate for synthesized visuals (fail closed)."""
    out: list = []
    try:
        val_ents = list(gate.get("validated_entities", []) or gate.get("entities", []) or [])
        val_mets = list(gate.get("validated_metrics", []) or gate.get("metrics", []) or [])
        years = gate.get("years")
        time_label = f"{years}Y" if years else None
        # When the gate did not apply, fall back to plan entities ONLY for
        # comparison queries. Own-data row visuals (metric-based, no entities)
        # must not be entity-gated, or every row chart would be rejected.
        if not val_ents and not gate.get("applies"):
            try:
                if build_research_plan is not None:
                    pd = dict(build_research_plan(query) or {})
                    if pd.get("is_comparison") and len(pd.get("entities", []) or []) >= 2:
                        val_ents = list(pd.get("entities", []) or [])
                        val_mets = list(pd.get("metrics", []) or [])
                        time_label = pd.get("period_label") or time_label
            except Exception:
                pass
        for visual in visuals or []:
            vtype = getattr(visual, "visual_type", "")
            _vtitle = str(getattr(visual, "title", "") or "").lower()
            _qualitative_table = vtype == "table" and any(
                k in _vtitle for k in ("source", "timeline", "outlook")
            )
            if vtype not in ("graph", "comparison", "table") or _qualitative_table:
                out.append(visual)
                continue
            try:
                ok, why = validate_visual_provenance(
                    visual, query=query,
                    validated_entities=val_ents,
                    validated_metrics=val_mets,
                    expected_timeframe=time_label,
                )
            except Exception as exc:
                ok, why = False, f"provenance check failed ({exc})"
            if ok:
                out.append(visual)
            else:
                logger.info("Rejected synthesized visual: %s.", why)
    except Exception as exc:
        logger.warning("Final provenance filter failed, stripping charts: %s", exc)
        out = [v for v in (visuals or []) if getattr(v, "visual_type", "") not in ("graph", "comparison")]
    return out


def build_validated_evidence_state(
    *,
    query: str,
    plan: Optional[Dict[str, Any]] = None,
    gate: Optional[Dict[str, Any]] = None,
    completeness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single canonical validated-evidence state for answer scope, stats,
    confidence, visual planner, visual validator, and narration.

    Merges plan (requested), gate (validated subset + stats), and
    completeness (per-entity coverage) into one dict. Components must read
    this instead of independently deciding whether evidence exists.
    """
    plan = dict(plan or {})
    gate = dict(gate or {})
    completeness = dict(completeness or {})
    entities = list(plan.get("entities", []) or gate.get("entities", []) or [])
    metrics = list(plan.get("metrics", []) or gate.get("metrics", []) or [])
    validated_entities = list(
        gate.get("validated_entities", []) or completeness.get("validated_entities", []) or []
    )
    validated_metrics = list(
        gate.get("validated_metrics", []) or completeness.get("validated_metrics", []) or []
    )
    # Fallback: completeness per_entity matrix.
    if not validated_entities and isinstance(completeness.get("per_entity"), dict):
        for entity, row in (completeness.get("per_entity") or {}).items():
            if isinstance(row, dict) and any(row.values()):
                validated_entities.append(entity)
    if not validated_metrics and isinstance(completeness.get("per_entity"), dict):
        seen: set[str] = set()
        for row in (completeness.get("per_entity") or {}).values():
            if isinstance(row, dict):
                for metric, ok in row.items():
                    if ok and metric not in seen:
                        seen.add(metric)
                        validated_metrics.append(metric)
    excluded_entities = list(
        gate.get("excluded_entities", []) or completeness.get("excluded_entities", []) or []
    )
    if not excluded_entities and entities:
        _val = {str(e).strip().lower() for e in validated_entities}
        excluded_entities = [e for e in entities if str(e).strip().lower() not in _val]
    sufficient = bool(gate.get("comparison_stats")) or bool(
        len(validated_entities) >= 2 and len(validated_metrics) >= 1
    )
    # Gate blocked overrides sufficiency (insufficient subset).
    if gate.get("applies") and gate.get("blocked"):
        sufficient = False
    complete = bool(
        entities and metrics
        and not excluded_entities
        and not (completeness.get("missing") or [])
        and not gate.get("blocked")
    )
    return {
        "query": query,
        "entities_requested": entities,
        "metrics_requested": metrics,
        # Canonical aliases (single state, two key styles during migration;
        # both always agree -- never two interpretations).
        "requested_entities": entities,
        "requested_metrics": metrics,
        "validated_entities": validated_entities,
        "validated_metrics": validated_metrics,
        "excluded_entities": excluded_entities,
        "excluded_metrics": list(completeness.get("excluded_metrics", []) or []),
        "sufficient": sufficient,
        "partial": bool(sufficient and (excluded_entities or gate.get("partial"))),
        "complete": complete,
        "blocked": bool(gate.get("blocked")),
        "comparison_stats": gate.get("comparison_stats"),
        "exclusion_note": str(
            gate.get("exclusion_note", "")
            or completeness.get("exclusion_note", "")
            or ""
        ),
    }


def plan_visuals_from_evidence(
    *,
    query: str,
    validated_state: Optional[Dict[str, Any]] = None,
    has_rows: bool = False,
    has_history: bool = False,
    has_market: bool = False,
    has_snippets: bool = False,
) -> List[str]:
    """Deterministic visual planner (P0#18): the ONE authoritative plan.

    Consumes user intent + canonical validated evidence + visual
    eligibility -- never arbitrary LLM suggestions. The judge's visual_plan
    stays advisory (reconciled + logged by the caller); this wins.
    """
    try:
        intent = _visual_intent(query or "")
        state = validated_state or {}
        blocked = bool(state.get("blocked"))
        sufficient = bool(state.get("sufficient"))
        if intent == "what_if":
            return ["comparison"] if not blocked else []
        if blocked:
            return []
        if state.get("comparison_stats") or (sufficient and has_history):
            # Metric-aware (P0#33): a price graph needs validated stock
            # evidence, tables need validated financial evidence -- never a
            # visual for an unvalidated metric.
            val_mets = {str(m).strip().lower() for m in (state.get("validated_metrics", []) or [])}
            kinds: List[str] = []
            if not val_mets or "stock_performance" in val_mets:
                kinds.append("graph")
            if not val_mets or val_mets & {"revenue_growth", "profitability"}:
                kinds.append("table")
            if state.get("comparison_stats"):
                kinds.append("comparison")
            return kinds
        if has_rows:
            return ["graph", "table", "metric"]
        if has_market:
            return ["graph"]
        if has_snippets:
            if intent == "comparison":
                return ["comparison", "table"]
            return ["table", "insight"]
        return []
    except Exception:
        return []


def apply_narration_contract(
    output: PipelineOutput,
    *,
    validated_state: Optional[Dict[str, Any]] = None,
    computed_numbers: Optional[dict] = None,
    gate: Optional[Dict[str, Any]] = None,
    thinking: Optional[list] = None,
) -> PipelineOutput:
    """Code-enforced narration + final validation + followup grounding.

    - Collects every validated number (evidence + deterministic computation)
      as the grounding pool; flags ungrounded numeric claims.
    - Winners must come from validated comparison stats; otherwise winner
      language is hedged (not silently replaced with a guess).
    - Excluded entities/metrics flagged when presented as included.
    - What-if assumption string must appear verbatim in the answer.
    - Follow-ups dropped when they resurrect excluded entities/metrics.
    - Final consistency actions applied (confidence caps, visual drops).
    Never raises; violations are logged and surfaced via thinking.
    """
    try:
        from app.services.data.canonical import (
            final_response_validation as _final,
            ground_followups as _ground_f,
            validate_narration as _validate,
        )
    except Exception:
        return output
    state = validated_state or {}
    computed_numbers = computed_numbers or {}
    gate = gate or {}
    try:
        pool = _evidence_numbers(
            snippets=None, rows=None,
            price_history=None, financial_history=None,
            market_data=None, computed_numbers=computed_numbers,
        )
        # Winners from the single validated source.
        stats = (computed_numbers.get("comparison_stats", {}) or {})
        if not stats:
            stats = (state.get("deterministic_statistics", {}) or {})
        _gate_stats = gate.get("comparison_stats") or {}
        winners = dict(
            stats.get("winners", {}) or _gate_stats.get("winners", {}) or {}
        )
        verdict = _validate(
            output.answer or "", validated_state=state,
            known_numbers=pool, winners=winners,
        )
        for violation in verdict.get("violations", []):
            logger.info("Narration contract violation: %s", violation[:200])
            if thinking is not None and "ungrounded number" in violation:
                thinking.append("Narration used a number outside validated evidence.")
            if thinking is not None and "winner" in violation:
                thinking.append("Winner claim lacks validated comparison support.")
        # What-if assumption verbatim check (P0#10): the code validates the
        # narrated assumption matches computation state (not just prompting).
        try:
            what_if = computed_numbers.get("what_if") or {}
            assumption = str(what_if.get("assumption", "") or "").strip()
            if assumption and output.clarification is None:
                norm_answer = " ".join((output.answer or "").lower().split())
                norm_assump = " ".join(assumption.lower().split())
                # Key content words of the assumption must appear verbatim
                # (elasticity disclaimer); otherwise append it structurally.
                if "elasticity" in norm_assump and "elasticity" not in norm_answer:
                    output.answer = str(output.answer or "").rstrip() + "\n\n" + assumption
                    if thinking is not None:
                        thinking.append("What-if assumption appended verbatim (was missing).")
        except Exception:
            pass
        # Final consistency validation (P0#23): apply safe actions only
        # (drop visuals, cap confidence) -- never guess replacements.
        try:
            final = _final(
                answer=output.answer or "",
                visuals=list(output.visuals or []),
                confidence=output.confidence,
                validated_state=state,
                known_numbers=pool,
                winners=winners,
            )
            for action in final.get("actions", []):
                if action == "set_confidence_zero":
                    output.confidence = 0.0
                    output.visuals = [
                        v for v in (output.visuals or [])
                        if getattr(v, "visual_type", "") not in ("graph", "comparison")
                    ]
                elif action == "cap_confidence_065":
                    output.confidence = min(float(output.confidence or 0.0), 0.65)
                elif action in ("drop_visual_with_excluded_entity", "drop_unvalidated_visual"):
                    # Drop only the offending visuals is ideal; conservatively
                    # provenance-filter all charts (fail closed, no guessing).
                    output.visuals = _provenance_filter_final(
                        list(output.visuals or []), state.get("query", ""), gate,
                    )
            for violation in final.get("violations", []):
                if thinking is not None:
                    thinking.append(f"Final validation: {violation[:140]}")
        except Exception:
            pass
        # Grounded follow-ups (P1#30).
        try:
            if output.followups:
                output.followups = _ground_f(list(output.followups or []), state)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Narration contract application failed: %s", exc)
    return output

__all__ = [
    "_VISUAL_NUMBER_RE",
    "_VISUAL_SCALE",
    "_attach_history_provenance",
    "_evidence_numbers",
    "_iter_visual_numbers",
    "_parse_scaled_number",
    "_provenance_filter_final",
    "_visual_entities",
    "_visual_numbers_grounded",
    "_visual_numbers_grounded_in_pool",
    "apply_narration_contract",
    "attach_provenance",
    "build_validated_evidence_state",
    "build_visual_provenance",
    "drop_ungrounded_visuals",
    "drop_ungrounded_visuals_evidence",
    "is_visual_stale_for_query",
    "plan_visuals_from_evidence",
    "validate_visual_provenance",
]
