"""Canonical pipeline contracts (generic, no question-specific logic).

Single enforcement layer for the items in the audit:

- canonical query (P0#1): one logical query for SQL/research/visuals/narration,
  clarification merged exactly once, stale-query detection.
- canonical evidence state (P0#2): requested/validated/excluded entities,
  metrics, timeframe, coverage/frequency/unit/currency/definition/geography/
  population, evidence records, provenance, comparison + comparability status,
  deterministic statistics/calculations, assumptions, visual eligibility,
  confidence basis.
- evidence requirements matrix (P0#3): entities x metrics x timeframe x data type.
- deterministic computation records (P0#9): inputs/operation/formula/
  assumptions/output/units/timeframe/source refs; explicit None vs 0 vs NaN.
- narration + final validation (P0#22/#23): code-enforced claim grounding.
- followup grounding (P1#30), thread isolation helpers (P0#20),
  JSON robustness (P2#36), citation safety (P2#35).

All helpers are pure and generic. No entity/metric names are hard-coded here.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

CANONICAL_SCHEMA_VERSION = "canonical-v1"
EVIDENCE_SCHEMA_VERSION = "evidence-v1"
PARSER_VERSION = "parser-v1"

_CLARIFICATION_FRAGMENT_RE = re.compile(
    r"\s*\[clarification answer:.*?\]\s*", re.IGNORECASE | re.DOTALL
)


def strip_clarification_fragments(text: str) -> str:
    """Remove previously merged clarification fragments (idempotence)."""
    try:
        return _CLARIFICATION_FRAGMENT_RE.sub(" ", text or "").strip()
    except Exception:
        return (text or "").strip()


def build_canonical_query(
    original_query: str,
    prior_clarification: Optional[str] = None,
    followup_query: Optional[str] = None,
) -> str:
    """One canonical logical query for the whole pipeline (P0#1).

    - Strips any previously merged "[clarification answer: ...]" fragments so
      repeated turns never accumulate clarification text.
    - Merges the follow-up answer exactly once after the stripped original.
    - The same logical question always yields the same canonical text, so SQL
      and research/visual timeframes cannot diverge.
    """
    base = strip_clarification_fragments(original_query or "")
    followup = (followup_query or "").strip()
    if not base:
        return followup
    if not followup:
        return base
    # If the follow-up already equals the base (or is contained), do not append.
    norm_base = "".join(c for c in base.lower() if c.isalnum())
    norm_follow = "".join(c for c in followup.lower() if c.isalnum())
    if not norm_follow or norm_follow in norm_base:
        return base
    return f"{base} [clarification answer: {followup}]"


def canonical_query_changed(old: str, new: str) -> Tuple[bool, str]:
    """Stale-query detection across canonical queries (entity/metric/timeframe).

    Compares decomposed structure via lightweight regex signals only (no
    import cycle with comparison.py). Returns (changed, reason).
    """
    try:
        from app.services.data.comparison import decompose_comparison_query

        cur = decompose_comparison_query(new or "") or {}
        prev = decompose_comparison_query(old or "") or {}
        cur_e = {str(e).strip().lower() for e in (cur.get("entities", []) or [])}
        prev_e = {str(e).strip().lower() for e in (prev.get("entities", []) or [])}
        if cur_e and prev_e and cur_e != prev_e:
            return True, f"entity set changed {sorted(prev_e)} -> {sorted(cur_e)}"
        cur_m = {str(m).strip().lower() for m in (cur.get("metrics", []) or [])}
        prev_m = {str(m).strip().lower() for m in (prev.get("metrics", []) or [])}
        if cur_m and prev_m and cur_m != prev_m:
            return True, f"metric set changed {sorted(prev_m)} -> {sorted(cur_m)}"
        if (cur.get("period_label") or "") != (prev.get("period_label") or ""):
            if cur.get("period_label") or prev.get("period_label"):
                return True, (
                    f"timeframe changed {prev.get('period_label')} -> "
                    f"{cur.get('period_label')}"
                )
        return False, ""
    except Exception as exc:
        return False, f"comparison unavailable ({exc})"


# ---------------------------------------------------------------------------
# Evidence requirements matrix (P0#3): entities x metrics x timeframe x type
# ---------------------------------------------------------------------------

def metric_data_type(metric: str) -> str:
    """Generic metric -> required data-type class (no hard-coded entities)."""
    m = (metric or "").strip().lower()
    if m in ("stock_performance",):
        return "market_history"
    if m in ("revenue_growth", "profitability"):
        return "financial_history"
    # Arbitrary future metrics resolve via research figures/snippets.
    return "research_figures"


@dataclass
class EvidenceRequirementItem:
    entity: str
    metric: str
    timeframe: Optional[str] = None
    data_type: str = "research_figures"
    status: str = "missing"  # fulfilled | missing | invalid | needs_recovery
    reason: str = ""


def derive_evidence_requirements(
    entities: Sequence[str],
    metrics: Sequence[str],
    timeframe_label: Optional[str] = None,
) -> List[EvidenceRequirementItem]:
    """Explicit requirement matrix before retrieval (generic)."""
    out: List[EvidenceRequirementItem] = []
    for entity in (entities or []):
        if not str(entity).strip():
            continue
        for metric in (metrics or []):
            if not str(metric).strip():
                continue
            out.append(
                EvidenceRequirementItem(
                    entity=str(entity),
                    metric=str(metric),
                    timeframe=timeframe_label,
                    data_type=metric_data_type(str(metric)),
                )
            )
    return out


def reconcile_requirements(
    requirements: Sequence[EvidenceRequirementItem],
    per_entity_metric_ok: Dict[str, Dict[str, bool]],
    reasons: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """After retrieval: fulfilled / missing / invalid / needs_recovery."""
    reasons = reasons or {}
    fulfilled: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    needs_recovery: List[Dict[str, Any]] = []
    for req in requirements or []:
        ok = bool((per_entity_metric_ok.get(req.entity) or {}).get(req.metric))
        entry = {
            "entity": req.entity,
            "metric": req.metric,
            "timeframe": req.timeframe,
            "data_type": req.data_type,
        }
        if ok:
            fulfilled.append(entry)
        else:
            reason = (
                reasons.get(f"{req.entity}::{req.metric}")
                or reasons.get(req.entity)
                or f"no validated evidence for {req.entity} / {req.metric}"
            )
            missing.append({**entry, "reason": reason})
            # Anything requested but not validated is a recovery candidate;
            # callers must not re-request already-fulfilled cells.
            needs_recovery.append({**entry, "reason": reason})
    return {
        "fulfilled": fulfilled,
        "missing": missing,
        "invalid": invalid,
        "needs_recovery": needs_recovery,
    }


# ---------------------------------------------------------------------------
# Canonical validated evidence state (P0#2)
# ---------------------------------------------------------------------------

COMPARISON_COMPLETE = "COMPLETE"
COMPARISON_PARTIAL = "PARTIAL"
COMPARISON_INSUFFICIENT = "INSUFFICIENT"
COMPARISON_BLOCKED = "BLOCKED"


def comparison_status_for(
    *,
    sufficient: bool,
    complete: bool,
    partial: bool,
    blocked: bool,
) -> str:
    if blocked or not sufficient:
        # Distinguish blocked (explicit gate) from merely insufficient.
        return COMPARISON_BLOCKED if blocked else COMPARISON_INSUFFICIENT
    if complete and not partial:
        return COMPARISON_COMPLETE
    return COMPARISON_PARTIAL


def build_canonical_evidence_state(
    *,
    query: str,
    requested_entities: Sequence[str],
    requested_metrics: Sequence[str],
    requested_timeframe: Optional[str],
    validated_entities: Sequence[str],
    validated_metrics: Sequence[str],
    validated_timeframe: Optional[str] = None,
    coverage: Optional[Dict[str, Any]] = None,
    frequency: Optional[str] = None,
    unit: Optional[str] = None,
    currency: Optional[str] = None,
    definition: Optional[str] = None,
    geography: Optional[str] = None,
    population: Optional[str] = None,
    evidence_records: Optional[List[Dict[str, Any]]] = None,
    source_ids: Optional[List[str]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    deterministic_statistics: Optional[Dict[str, Any]] = None,
    deterministic_calculations: Optional[List[Dict[str, Any]]] = None,
    assumptions: Optional[List[str]] = None,
    visual_eligibility: Optional[Dict[str, Any]] = None,
    confidence_basis: Optional[Dict[str, Any]] = None,
    comparability_status: Optional[Dict[str, Any]] = None,
    blocked: bool = False,
    blocked_reason: str = "",
) -> Dict[str, Any]:
    """The SINGLE SOURCE OF TRUTH downstream consumers must read.

    Never maintains two interpretations of the same fact: excluded entities
    are derived structurally (requested minus validated), not by string
    matching in prose.
    """
    req_ents = [str(e) for e in (requested_entities or []) if str(e).strip()]
    req_mets = [str(m) for m in (requested_metrics or []) if str(m).strip()]
    val_ents = [str(e) for e in (validated_entities or []) if str(e).strip()]
    val_mets = [str(m) for m in (validated_metrics or []) if str(m).strip()]
    val_set = {e.strip().lower() for e in val_ents}
    excluded_entities = [e for e in req_ents if e.strip().lower() not in val_set]
    val_mset = {m.strip().lower() for m in val_mets}
    excluded_metrics = [m for m in req_mets if m.strip().lower() not in val_mset]
    sufficient = bool(len(val_ents) >= 2 and len(val_mets) >= 1) if len(req_ents) >= 2 else bool(
        len(val_ents) >= 1 and len(val_mets) >= 1
    )
    if len(req_ents) == 1 and req_mets:
        sufficient = bool(val_ents and val_mets)
    complete = bool(req_ents and req_mets and not excluded_entities and not excluded_metrics)
    partial = bool(sufficient and not complete)
    if blocked:
        sufficient = False
        partial = False
        complete = False
    status = comparison_status_for(
        sufficient=sufficient, complete=complete, partial=partial, blocked=blocked
    )
    requirements = derive_evidence_requirements(req_ents, req_mets, requested_timeframe)
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "query": query,
        "requested_entities": req_ents,
        "validated_entities": val_ents,
        "excluded_entities": [
            {"entity": e, "reason": f"no validated evidence for {e}"}
            for e in excluded_entities
        ],
        "requested_metrics": req_mets,
        "validated_metrics": val_mets,
        "excluded_metrics": [
            {"metric": m, "reason": f"no validated evidence for {m}"}
            for m in excluded_metrics
        ],
        "requested_timeframe": requested_timeframe,
        "validated_timeframe": validated_timeframe or requested_timeframe,
        "coverage": coverage or {},
        "frequency": frequency or "UNKNOWN",
        "unit": unit or "UNKNOWN",
        "currency": currency or "UNKNOWN",
        "definition": definition or "UNKNOWN",
        "geography": geography or "UNKNOWN",
        "population": population or "UNKNOWN",
        "evidence_records": evidence_records or [],
        "source_ids": source_ids or [],
        "provenance": provenance or {},
        "comparison_status": status,
        "comparability_status": comparability_status or {"ok": True, "detail": ""},
        "deterministic_statistics": deterministic_statistics or {},
        "deterministic_calculations": deterministic_calculations or [],
        "assumptions": assumptions or [],
        "visual_eligibility": visual_eligibility or {"eligible": sufficient and not blocked},
        "confidence_basis": confidence_basis or {},
        "requirements": [
            {
                "entity": r.entity,
                "metric": r.metric,
                "timeframe": r.timeframe,
                "data_type": r.data_type,
            }
            for r in requirements
        ],
        "sufficient": sufficient,
        "partial": partial,
        "complete": complete,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "exclusion_disclosed": False,
    }


def mark_exclusion_disclosed(state: Dict[str, Any]) -> Dict[str, Any]:
    """Structural exclusion-note dedup flag (no fragile string matching)."""
    try:
        state["exclusion_disclosed"] = True
    except Exception:
        pass
    return state


def exclusion_note_for(state: Dict[str, Any]) -> str:
    """Single renderer for the exclusion disclosure (P0#5 dedup)."""
    try:
        bits: List[str] = []
        for item in state.get("excluded_entities", []) or []:
            name = item.get("entity") if isinstance(item, dict) else str(item)
            if name:
                bits.append(str(name))
        for item in state.get("excluded_metrics", []) or []:
            name = item.get("metric") if isinstance(item, dict) else str(item)
            if name:
                bits.append(str(name))
        if not bits:
            return ""
        return (
            "Excluded from this comparison (insufficient validated evidence): "
            + "; ".join(bits)
            + "."
        )
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Deterministic computation records (P0#9)
# ---------------------------------------------------------------------------

def computation_record(
    *,
    operation: str,
    inputs: Dict[str, Any],
    output: Any,
    formula: str = "",
    assumptions: Optional[List[str]] = None,
    units: Optional[str] = None,
    timeframe: Optional[str] = None,
    sources: Optional[List[str]] = None,
    computation_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "computation_id": computation_id or operation,
        "operation": operation,
        "inputs": inputs,
        "formula": formula,
        "assumptions": list(assumptions or []),
        "output": output,
        "units": units or "UNKNOWN",
        "timeframe": timeframe,
        "sources": list(sources or []),
    }


def is_missing_value(value: Any) -> bool:
    """Explicit None/missing/NaN/empty/invalid vs valid 0 distinction."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.strip() == ""
    try:
        if isinstance(value, float) and math.isnan(value):
            return True
    except Exception:
        return True
    return False


def simple_pct_change(start: Any, end: Any) -> Optional[float]:
    """Simple end-to-end percentage change (NOT CAGR). Explicit zero handling."""
    try:
        if is_missing_value(start) or is_missing_value(end):
            return None
        start_f = float(start)  # type: ignore[arg-type]
        end_f = float(end)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start_f) or not math.isfinite(end_f):
        return None
    if start_f == 0:
        return None  # undefined, never 0 or inf
    return round((end_f - start_f) / abs(start_f) * 100, 2)


SIMPLE_CHANGE_LABEL = "simple percentage change (end vs start; not CAGR)"


def compute_cagr(start: Any, end: Any, periods: Any) -> Optional[float]:
    """Deterministic CAGR with explicit period semantics (percent, 2dp).

    CAGR = (end/start)^(1/periods) - 1, in percent. Returns None when
    undefined (missing/non-positive start/end, non-positive periods).
    """
    try:
        if is_missing_value(start) or is_missing_value(end) or is_missing_value(periods):
            return None
        start_f = float(start)  # type: ignore[arg-type]
        end_f = float(end)  # type: ignore[arg-type]
        n = float(periods)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f) and math.isfinite(n)):
        return None
    if start_f <= 0 or end_f <= 0 or n <= 0:
        return None
    try:
        return round(((end_f / start_f) ** (1.0 / n) - 1.0) * 100, 2)
    except Exception:
        return None


CAGR_FORMULA = "CAGR = (end/start)^(1/periods) - 1, in percent"


# ---------------------------------------------------------------------------
# Narration + final validation (P0#22 / P0#23)
# ---------------------------------------------------------------------------

_WINNER_WORDS_RE = re.compile(
    r"\b(best|winner|winners|outperform\w*|beat\w*|grew faster|improved|"
    r"strongest|highest|lowest|all companies|all metrics|every company)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"([$€₹£])?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand|%|percent)?"
)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _numbers_in_text(text: str, ignore_tokens: Sequence[str] = ()) -> List[float]:
    # Entity-name digits ("E1", "Series 2") are identifiers, not claims:
    # blank out known entity tokens before extracting numbers so validated
    # entity mentions never read as ungrounded values.
    cleaned = text or ""
    try:
        for token in sorted((ignore_tokens or []), key=len, reverse=True):
            if token and len(str(token)) >= 2:
                cleaned = re.sub(re.escape(str(token)), " ", cleaned, flags=re.IGNORECASE)
    except Exception:
        cleaned = text or ""
    out: List[float] = []
    for match in _NUMBER_RE.finditer(cleaned):
        raw = (match.group(2) or "").replace(",", "")
        if not match.group(1) and not match.group(3):
            # Bare numbers that are years are dates, not claims.
            if _YEAR_RE.fullmatch(raw):
                continue
            if len(raw) == 4 and raw.startswith(("19", "20")):
                continue
        try:
            amount = float(raw)
        except ValueError:
            continue
        scale = {"k": 1e3, "K": 1e3, "thousand": 1e3, "M": 1e6,
                 "million": 1e6, "B": 1e9, "billion": 1e9}.get(match.group(3) or "", 1)
        out.append(amount * scale)
    return out


def validate_narration(
    answer: str,
    *,
    validated_state: Dict[str, Any],
    known_numbers: Sequence[float] = (),
    winners: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Code-enforced narration contract (P0#22). Returns verdict + violations.

    Checks numbers, entities, winners, comparisons, timeframe, exclusions.
    Never raises; violations list drives the final validator (strip/block).
    """
    violations: List[str] = []
    answer_text = answer or ""
    lowered = answer_text.lower()
    try:
        validated_entities = [str(e) for e in (validated_state.get("validated_entities", []) or [])]
        requested_entities = [str(e) for e in (_requested_entities(validated_state) or [])]
        excluded = []
        for item in validated_state.get("excluded_entities", []) or []:
            excluded.append(item.get("entity") if isinstance(item, dict) else str(item))
        # Entities: named entities must be requested or validated (or explicitly
        # introduced by validated research sources -- approximated by validated set).
        allowed = {e.strip().lower() for e in (requested_entities + validated_entities) if e.strip()}
        # Full-coverage claims ("all four", "all companies", "every
        # company") require full validated evidence -- checked independently
        # of winner language. Partial answers must use subset language
        # ("among A, B and C").
        requested_n = len(requested_entities)
        validated_n = len(validated_entities)
        _full_coverage_claimed = bool(
            re.search(r"\ball\b.{0,30}?\b(compan\w*|metric\w*|entit\w*)\b", lowered)
            or re.search(r"\bevery\b.{0,30}?\b(compan\w*|metric\w*|entit\w*)\b", lowered)
            or (requested_n > validated_n and re.search(
                r"\ball\s+(four|4|three|3|five|5|both)\b", lowered
            ))
        )
        if _full_coverage_claimed and requested_n > validated_n:
            violations.append("full-coverage claim without full validated evidence")
        # Winner claims require validated comparison stats.
        winners = winners or {}
        if _WINNER_WORDS_RE.search(answer_text):
            if not winners and not validated_state.get("deterministic_statistics"):
                violations.append("winner/comparison claim without validated comparison stats")
            # A winner name must be a validated entity.
            for metric, winner in (winners or {}).items():
                if winner and winner.strip().lower() not in {e.strip().lower() for e in validated_entities}:
                    violations.append(f"winner {winner!r} not in validated entities")
        # Excluded entities must not be presented as included.
        for name in excluded:
            if name and name.strip().lower() in lowered:
                # Mentioning an exclusion ("excluding D") is allowed; claiming
                # D's numbers/winner status is not. Flag only value claims.
                window = lowered
                if re.search(rf"{re.escape(name.strip().lower())}\s*(grew|rose|fell|won|best|outperform|revenue|profit|margin|%)", window):
                    violations.append(f"excluded entity {name!r} used in a value claim")
        # Timeframe: requested vs narrated years should match when both explicit.
        requested_tf = str(validated_state.get("requested_timeframe", "") or "")
        validated_tf = str(validated_state.get("validated_timeframe", "") or requested_tf)
        if requested_tf and validated_tf and requested_tf != validated_tf:
            # Downgrade must be disclosed, not silent.
            if "snapshot" not in lowered and "insufficient" not in lowered and "partial" not in lowered:
                violations.append("timeframe downgrade not disclosed")
        # Numbers: every displayed money/percent number should be traceable.
        if known_numbers:
            _ignore = list(validated_entities) + list(requested_entities)
            for value in _numbers_in_text(answer_text, _ignore):
                grounded = any(
                    abs(c - value) <= max(1e-6, abs(value) * 0.05, abs(c) * 0.05)
                    for c in known_numbers
                )
                if not grounded:
                    violations.append(f"ungrounded number {value}")
                    break  # one representative violation is enough to gate
    except Exception as exc:
        violations.append(f"narration validation unavailable ({exc})")
    return {"ok": not violations, "violations": violations}


def _requested_entities(state: Dict[str, Any]) -> List[str]:
    """Read requested entities across state shapes (canonical + legacy)."""
    try:
        ents = list(state.get("requested_entities", []) or [])
        if not ents:
            ents = list(state.get("entities_requested", []) or [])
        return ents
    except Exception:
        return []


def _requested_metrics(state: Dict[str, Any]) -> List[str]:
    try:
        mets = list(state.get("requested_metrics", []) or [])
        if not mets:
            mets = list(state.get("metrics_requested", []) or [])
        return mets
    except Exception:
        return []


def requested_entities_default(state: Dict[str, Any]) -> List[str]:
    return _requested_entities(state)


def final_response_validation(
    *,
    answer: str,
    visuals: Sequence[Any],
    confidence: float,
    validated_state: Dict[str, Any],
    known_numbers: Sequence[float] = (),
    winners: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Final consistency validation over answer/visuals/confidence (P0#23).

    Returns {"ok", "violations", "actions"} where actions suggests
    remove/block operations the caller must apply (never guess replacements).
    """
    violations: List[str] = []
    actions: List[str] = []
    try:
        narr = validate_narration(
            answer, validated_state=validated_state,
            known_numbers=known_numbers, winners=winners,
        )
        violations.extend(narr.get("violations", []))
        # Visuals: every visual entity must be validated; excluded never in visuals.
        val_set = {str(e).strip().lower() for e in (validated_state.get("validated_entities", []) or [])}
        excluded_set = set()
        for item in validated_state.get("excluded_entities", []) or []:
            name = item.get("entity") if isinstance(item, dict) else str(item)
            if name:
                excluded_set.add(str(name).strip().lower())
        for visual in visuals or []:
            try:
                prov = getattr(visual, "provenance", None) or {}
                for ent in prov.get("entities", []) or []:
                    if str(ent).strip().lower() in excluded_set:
                        violations.append(f"visual contains excluded entity {ent!r}")
                        actions.append("drop_visual_with_excluded_entity")
                        break
                if val_set and (prov.get("entities") or []):
                    for ent in prov.get("entities", []) or []:
                        el = str(ent).strip().lower()
                        if el and el not in val_set and el not in excluded_set:
                            # Unknown entity in visual (not requested/validated).
                            violations.append(f"visual entity {ent!r} not validated")
                            actions.append("drop_unvalidated_visual")
                            break
            except Exception as exc:
                violations.append(f"visual validation unavailable ({exc})")
                actions.append("drop_unvalidated_visual")
        # Confidence compatibility.
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            violations.append("confidence not numeric")
            actions.append("set_confidence_zero")
            conf = 0.0
        if validated_state.get("blocked") and conf != 0.0:
            violations.append("blocked evidence must have confidence 0")
            actions.append("set_confidence_zero")
        if validated_state.get("partial") and conf > 0.65 + 1e-9:
            violations.append("partial evidence confidence exceeds 0.65")
            actions.append("cap_confidence_065")
    except Exception as exc:
        violations.append(f"final validation unavailable ({exc})")
    return {"ok": not violations, "violations": violations, "actions": actions}


# ---------------------------------------------------------------------------
# Followups (P1#30): grounded in canonical state, never resurrect exclusions
# ---------------------------------------------------------------------------

def ground_followups(
    followups: Sequence[str],
    validated_state: Dict[str, Any],
) -> List[str]:
    """Drop follow-ups involving excluded entities/metrics or stale timeframes."""
    out: List[str] = []
    try:
        excluded_ents = set()
        for item in validated_state.get("excluded_entities", []) or []:
            name = item.get("entity") if isinstance(item, dict) else str(item)
            if name:
                excluded_ents.add(str(name).strip().lower())
        excluded_mets = set()
        for item in validated_state.get("excluded_metrics", []) or []:
            name = item.get("metric") if isinstance(item, dict) else str(item)
            if name:
                excluded_mets.add(str(name).strip().lower())
        for followup in (followups or [])[:3]:
            text = str(followup or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if any(ent and ent in lowered for ent in excluded_ents):
                continue
            if any(met and met.replace("_", " ") in lowered for met in excluded_mets):
                continue
            out.append(text)
    except Exception:
        return [str(f) for f in (followups or [])[:3] if str(f).strip()]
    return out


# ---------------------------------------------------------------------------
# Thread isolation (P0#20): minimum scoped identifier, no global latest row
# ---------------------------------------------------------------------------

def scope_key(user_id: Any, thread_id: Optional[str] = None) -> str:
    """Minimum necessary scoped identifier for thread isolation."""
    base = str(user_id or "anon")
    thread = (thread_id or "default").strip() or "default"
    return f"{base}::thread::{thread}"


# ---------------------------------------------------------------------------
# Citation safety (P2#35) + JSON robustness (P2#36)
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"\[(\d+)\]")


def sanitize_citations_safe(answer: str, source_count: int) -> str:
    """Drop citation markers pointing at no source, preserve year brackets.

    [2024] (a year) is never a citation: only 1-2 digit markers (or numbers
    within the source range) are treated as citations.
    """
    text = answer or ""
    if source_count <= 0:
        def _drop_year_safe(match: "re.Match[str]") -> str:
            number = match.group(1)
            # Preserve 4-digit years (1900-2099) and long numbers.
            if len(number) >= 4:
                return match.group(0)
            try:
                if 1900 <= int(number) <= 2099:
                    return match.group(0)
            except ValueError:
                return match.group(0)
            return ""
        return _CITATION_RE.sub(_drop_year_safe, text)

    def _keep(match: "re.Match[str]") -> str:
        raw = match.group(1)
        # Years are never citations.
        if len(raw) >= 4:
            return match.group(0)
        try:
            number = int(raw)
        except ValueError:
            return match.group(0)
        return match.group(0) if 1 <= number <= source_count else ""

    return _CITATION_RE.sub(_keep, text)


def extract_json_robust(text: str) -> dict:
    """Harden JSON extraction beyond first-{ to last-} (P2#36).

    Tries: whole-text parse, fenced parse, then balanced-brace scan for the
    first valid JSON object. Raises ValueError when nothing parses.
    """
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Balanced-brace scan: find each { ... } candidate with string awareness.
    candidates: List[str] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False
    for index, char in enumerate(cleaned):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(cleaned[start : index + 1])
                    start = None
    # Prefer the longest valid object (usually the full payload, not a nested one).
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    # Legacy fallback: first { through last }.
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            parsed = json.loads(cleaned[start_idx : end_idx + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    raise ValueError(f"Could not extract valid JSON from LLM response: {cleaned[:200]}")
