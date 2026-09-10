"""Historical-comparison gate + what-if computation. Split from langchain_pipeline.py; behavior unchanged."""

import logging
import re

from typing import Any, Dict, List, Optional, Tuple

from .deps import ComparisonEvidence, METRIC_PROFIT, METRIC_REVENUE, METRIC_STOCK, compute_comparison_stats, compute_net_margins, compute_yearly_stats, decompose_comparison_query, insufficient_reason, validate_comparison, validate_historical_coverage

logger = logging.getLogger(__name__)



def _historical_comparison_gate(
    query: str,
    market_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    fundamentals: Optional[list] = None,
) -> Dict[str, Any]:
    """Evaluate whether a historical comparison may be charted.

    Returns a dict with: entities/metrics/years, per-metric evidence
    presence, historical_ok + detail, comparison_ok + detail, blocked
    (bool), blocked_reason, and deterministic comparison_stats when the
    evidence validates. Pure gating -- never fabricates data.
    """
    empty: Dict[str, Any] = {
        "applies": False, "blocked": False, "blocked_reason": "",
        "entities": [], "metrics": [], "years": None,
        "historical_ok": True, "historical_detail": "",
        "comparison_ok": True, "comparison_detail": "",
        "comparison_stats": None,
    }
    try:
        if decompose_comparison_query is None:
            return empty
        decomposed = decompose_comparison_query(query or "") or {}
    except Exception:
        return empty
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    years = decomposed.get("period_years")
    is_comp = bool(decomposed.get("is_comparison"))
    requires_history = bool(decomposed.get("requires_history"))
    # Gate applies to named multi-entity comparisons (historical or not):
    # single-series and mismatched evidence must never chart as "comparison".
    if not (is_comp and len(entities) >= 2):
        return empty
    # Scope early-out: when NO requested entity can enter market adapters
    # (all countries/geographies, all private/unlisted companies, all
    # concepts...), this market-evidence gate is inapplicable -- the
    # question is a qualitative/snippet comparison, not a Yahoo-chartable
    # one. Without this, "Compare America and Britain" burns through
    # stock/revenue validation and fails loudly with exclusion boilerplate
    # for entities that were never market candidates.
    try:
        from app.services.data.comparison import (
            market_candidate_entities as _market_candidates,
        )

        if not _market_candidates(entities):
            return {**empty, "entities": entities, "metrics": metrics, "years": years}
    except Exception:
        pass
    gate: Dict[str, Any] = {
        **empty, "applies": True, "entities": entities,
        "metrics": metrics, "years": years,
    }
    if not requires_history:
        # Non-historical named comparison: only enforce entity coverage
        # (both sides present) -- no period validation needed.
        have = set()
        for lst in (market_data or [], price_history or []):
            for item in lst:
                if isinstance(item, dict) and item.get("values"):
                    have.add(str(item.get("entity", "")).strip().lower())
        want = {str(e).strip().lower() for e in entities}
        if not want.issubset(have):
            # Fall back to snippet-figure attribution downstream; do not
            # hard-block non-historical comparisons here (figures may still
            # carry both sides). Mark not-blocked so old paths run.
            pass
        return gate

    # Historical path: need structured multi-year evidence for EVERY
    # requested metric. Snippets alone never suffice.
    price_history = list(price_history or [])
    financial_history = list(financial_history or [])
    details: list[str] = []
    comp_details: list[str] = []
    historical_ok = True
    comparison_ok = True

    def _entities_with_price() -> set[str]:
        return {
            str(item.get("entity", "")).strip().lower()
            for item in price_history
            if isinstance(item, dict) and item.get("values") and item.get("labels")
        }

    def _entities_with_fin(block: str) -> set[str]:
        out: set[str] = set()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            chunk = item.get(block, {})
            if isinstance(chunk, dict) and chunk.get("values") and chunk.get("labels"):
                out.add(str(item.get("entity", "")).strip().lower())
        return out

    want = {str(e).strip().lower() for e in entities}
    stats_input: Dict[str, Dict[str, Tuple[Any, Any]]] = {}
    scope_word = f"all {len(entities)}" if len(entities) > 2 else "both"
    # Partial tracking: per-metric validated subsets (never zero-filled).
    validated_by_metric: Dict[str, List[str]] = {}
    excluded_by_metric: Dict[str, List[str]] = {}

    # -- stock performance: multi-year price history (validated subset) --
    if METRIC_STOCK in metrics:
        have = _entities_with_price()
        missing_stock = sorted(want - have)
        # Coverage-filtered subset: only entities with history + span.
        covered: List[dict] = []
        for item in price_history:
            ok, detail = validate_historical_coverage(
                labels=list(item.get("labels") or []),
                period_start=item.get("period_start"),
                period_end=item.get("period_end"),
                requested_years=years,
                values=item.get("values"),
            )
            if not ok:
                details.append(f"{item.get('entity')}: {detail}")
            else:
                covered.append(item)
        if missing_stock:
            details.append(
                f"stock price history missing for {missing_stock} "
                f"(have {sorted(have)})."
            )
        if len(covered) >= 2:
            # Like-for-like among the COVERED subset only (not all requested).
            ev = []
            for item in covered:
                vals = list(item.get("values") or [])
                labs = list(item.get("labels") or [])
                if ComparisonEvidence is None:
                    continue
                try:
                    ev.append(ComparisonEvidence(
                        entity=str(item.get("entity", "")),
                        metric=METRIC_STOCK,
                        value=float(vals[-1]),
                        unit=str(item.get("currency", "price") or "price"),
                        period_start=str(labs[0]) if labs else None,
                        period_end=str(labs[-1]) if labs else None,
                        frequency=str(item.get("frequency", "weekly") or "weekly"),
                        definition=str(item.get("metric", "close") or "close"),
                        source="Yahoo Finance",
                        source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/history/",
                        is_historical=True,
                    ))
                except (TypeError, ValueError):
                    continue
            # Validate subset like-for-like (no expected_entities=all gate).
            subset_names = [str(item.get("entity", "")) for item in covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_STOCK,
                                             enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 stock series")
            if not ok:
                comp_details.append(f"stock: {detail}")
            else:
                for item in covered:
                    vals = list(item.get("values") or [])
                    stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_STOCK] = (vals[0], vals[-1])
                validated_by_metric[METRIC_STOCK] = [str(i.get("entity", "")) for i in covered]
        else:
            comp_details.append(f"stock comparison lacks {scope_word} companies (only {len(covered)} validated).")
        # Excluded for this metric: requested minus validated.
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_STOCK, [])}
        excluded_by_metric[METRIC_STOCK] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # -- revenue growth: annual revenue history (validated subset) --
    if METRIC_REVENUE in metrics:
        have = _entities_with_fin("revenue")
        missing_rev = sorted(want - have)
        if missing_rev:
            details.append(f"annual revenue history missing for {missing_rev}.")
        # Subset with 2+ annual points.
        rev_covered = []
        for item in financial_history:
            chunk = item.get("revenue", {})
            vals = list(chunk.get("values") or [])
            if len(vals) >= 2:
                rev_covered.append(item)
            else:
                details.append(f"{item.get('entity')}: need 2+ annual revenue points.")
        if len(rev_covered) >= 2:
            ev = []
            for item in rev_covered:
                chunk = item.get("revenue", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if ComparisonEvidence is not None:
                    _ccy = str(
                            chunk.get("currency", "") or item.get("currency", "") or ""
                        ).strip().upper() or None
                    try:
                        ev.append(ComparisonEvidence(
                            entity=str(item.get("entity", "")),
                            metric=METRIC_REVENUE,
                            value=float(vals[-1]),
                            unit=_ccy or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual",
                            definition=str(chunk.get("metric", "annualTotalRevenue")),
                            source="Yahoo Finance",
                            source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                            is_historical=True,
                            currency=_ccy,
                            source_type="yahoo" if item.get("provenance") != "web_snippets" else "web_snippets",
                            provenance=str(item.get("provenance", "") or "") or None,
                        ))
                    except (TypeError, ValueError):
                        continue
            subset_names = [str(item.get("entity", "")) for item in rev_covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_REVENUE,
                                       enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 revenue series")
            if not ok:
                comp_details.append(f"revenue: {detail}")
            else:
                for item in rev_covered:
                    vals = list((item.get("revenue", {}) or {}).get("values") or [])
                    if len(vals) >= 2:
                        stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_REVENUE] = (vals[0], vals[-1])
                validated_by_metric[METRIC_REVENUE] = [str(i.get("entity", "")) for i in rev_covered]
        else:
            comp_details.append(f"revenue comparison lacks {scope_word} companies (only {len(rev_covered)} validated).")
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_REVENUE, [])}
        excluded_by_metric[METRIC_REVENUE] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # -- profitability: ONE shared annual metric (validated subset) --
    # (net profit margin = net income / revenue * 100, computed in code).
    if METRIC_PROFIT in metrics:
        have = _entities_with_fin("net_income")
        missing_prof = sorted(want - have)
        if missing_prof:
            details.append(f"annual profitability history missing for {missing_prof}.")
        prof_covered = []
        for item in financial_history:
            chunk = item.get("net_income", {})
            vals = list(chunk.get("values") or [])
            if len(vals) >= 2:
                prof_covered.append(item)
            else:
                details.append(f"{item.get('entity')}: need 2+ annual profit points.")
        if len(prof_covered) >= 2:
            ev = []
            for item in prof_covered:
                chunk = item.get("net_income", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if ComparisonEvidence is not None:
                    _ccy2 = str(
                            chunk.get("currency", "") or item.get("currency", "") or ""
                        ).strip().upper() or None
                    try:
                        ev.append(ComparisonEvidence(
                            entity=str(item.get("entity", "")),
                            metric=METRIC_PROFIT,
                            value=float(vals[-1]),
                            unit=_ccy2 or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual",
                            definition=str(chunk.get("metric", "annualNetIncome")),
                            source="Yahoo Finance",
                            source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                            is_historical=True,
                            currency=_ccy2,
                            source_type="yahoo" if item.get("provenance") != "web_snippets" else "web_snippets",
                            provenance=str(item.get("provenance", "") or "") or None,
                        ))
                    except (TypeError, ValueError):
                        continue
            subset_names = [str(item.get("entity", "")) for item in prof_covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_PROFIT,
                                       enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 profit series")
            if not ok:
                comp_details.append(f"profitability: {detail}")
            else:
                for item in prof_covered:
                    vals = list((item.get("net_income", {}) or {}).get("values") or [])
                    if len(vals) >= 2:
                        stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_PROFIT] = (vals[0], vals[-1])
                validated_by_metric[METRIC_PROFIT] = [str(i.get("entity", "")) for i in prof_covered]
        else:
            comp_details.append(f"profitability comparison lacks {scope_word} companies (only {len(prof_covered)} validated).")
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_PROFIT, [])}
        excluded_by_metric[METRIC_PROFIT] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # A 1-month market_data series present WITHOUT price history is the
    # exact live failure: flag it explicitly, never let it satisfy history.
    # It invalidates the STOCK metric subset (other metrics may still be
    # sufficient for a partial answer).
    if not price_history and (market_data or []) and METRIC_STOCK in metrics:
        details.append(
            "only a short-term (one-month) price series is available; "
            "it cannot satisfy a multi-year request."
        )
        comp_details.append("stock: short-term series cannot satisfy history.")
        validated_by_metric.pop(METRIC_STOCK, None)
        excluded_by_metric[METRIC_STOCK] = list(entities)

    # Partial sufficiency: at least one requested metric with >=2 validated
    # entities whose subset passed like-for-like. Missing entities/metrics
    # are excluded with reasons, never zero-filled.
    validated_metrics = sorted(validated_by_metric.keys())
    validated_entities_union = sorted({
        e for ents in validated_by_metric.values() for e in ents
    })
    sufficient = bool(validated_metrics and len(validated_entities_union) >= 2)
    # Historical/comparison verdicts describe the VALIDATED SUBSET, not the
    # full request: True when sufficient, False only when insufficient.
    historical_ok = bool(sufficient)
    comparison_ok = bool(sufficient)
    # Preserve explicit subset failures (e.g. mixed frequencies) as not-ok.
    # If every validated subset failed validation, validated_by_metric would
    # be empty and sufficient False -- already covered.
    gate["historical_ok"] = historical_ok
    gate["historical_detail"] = " ".join(details)
    gate["comparison_ok"] = comparison_ok
    gate["comparison_detail"] = " ".join(comp_details)
    gate["validated_entities"] = validated_entities_union
    gate["validated_metrics"] = validated_metrics
    gate["validated_by_metric"] = {k: list(v) for k, v in validated_by_metric.items()}
    gate["excluded_by_metric"] = {k: list(v) for k, v in excluded_by_metric.items()}
    # Union excluded entities: requested minus validated union.
    _val_lower = {str(e).strip().lower() for e in validated_entities_union}
    gate["excluded_entities"] = [
        e for e in entities if str(e).strip().lower() not in _val_lower
    ]
    _any_metric_excluded = any(
        bool(v) for v in (excluded_by_metric or {}).values()
    )
    gate["partial"] = bool(sufficient and (
        len(gate["excluded_entities"]) > 0
        or _any_metric_excluded
        or len(validated_metrics) < len([m for m in metrics if m in (METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT)])
    ))
    # Currency transparency (Phase 11): growth % and net margins are
    # currency-invariant (per-entity math), but absolute values in KNOWN
    # different currencies must never be read as like-for-like. Record the
    # mix for assumptions/table titles; absolute-value outputs enforce it.
    currency_sets: Dict[str, set] = {}
    try:
        for item in price_history:
            code = str(item.get("currency", "") or "").strip().upper()
            if code:
                currency_sets.setdefault("stock", set()).add(code)
        for item in financial_history:
            for block_key in ("revenue", "net_income"):
                block = (item or {}).get(block_key, {}) or {}
                code = str(
                    block.get("currency", "") or item.get("currency", "") or ""
                ).strip().upper()
                if code:
                    currency_sets.setdefault(block_key, set()).add(code)
    except Exception:
        pass
    mixed = {key: sorted(codes) for key, codes in currency_sets.items() if len(codes) > 1}
    gate["currency_mixed"] = mixed
    gate["currency_detail"] = (
        "Reported currencies differ across companies "
        f"{mixed}: growth % and net margins compare currency-invariant; "
        "absolute values do not compare without explicit FX conversion."
        if mixed else ""
    )
    gate["blocked"] = not (historical_ok and comparison_ok)
    # Exclusion transparency for partial answers (never silent, never raw
    # repr): structured entity/metric lists rendered through the single
    # canonical renderer (exclusion_note_for) -- no f"{list}" interpolation
    # anywhere near user-facing text.
    try:
        from app.services.data.canonical import exclusion_note_for as _excl_for

        _validated_by_metric = validated_by_metric or {}
        _fully_excluded = list(gate.get("excluded_entities", []) or [])
        _fully_lower = {str(e).strip().lower() for e in _fully_excluded}
        # A metric with zero validated entities is excluded as a whole;
        # a metric validated for the subset names who lacks it per entity
        # ("Umbrella (revenue_growth, profitability)"), so partial gaps
        # are never silent and never raw repr.
        _metric_bits: list = []
        _partial_map: dict = {}
        for metric, excluded in (excluded_by_metric or {}).items():
            if not excluded:
                continue
            if _validated_by_metric.get(metric):
                for entity in excluded:
                    if str(entity).strip().lower() not in _fully_lower:
                        _partial_map.setdefault(str(entity), []).append(str(metric))
            else:
                _metric_bits.append({"metric": str(metric)})
        _entity_bits = [{"entity": entity} for entity in _fully_excluded]
        for entity, metrics in _partial_map.items():
            _entity_bits.append({"entity": f"{entity} ({', '.join(metrics)})"})
        gate["excluded_metrics"] = [
            bit["metric"] for bit in _metric_bits
        ]
        gate["exclusion_note"] = (
            _excl_for(
                {
                    "excluded_entities": _entity_bits,
                    "excluded_metrics": _metric_bits,
                }
            )
            if gate.get("partial")
            else ""
        )
    except Exception:
        gate["exclusion_note"] = ""
    if gate["blocked"] and insufficient_reason is not None:
        try:
            gate["blocked_reason"] = insufficient_reason(
                query=query, entities=entities, metrics=metrics,
                requested_years=years, historical_ok=historical_ok,
                historical_detail=gate["historical_detail"],
                comparison_ok=comparison_ok,
                comparison_detail=gate["comparison_detail"],
            )
        except Exception:
            gate["blocked_reason"] = "insufficient validated evidence."
    elif not gate["blocked"] and compute_comparison_stats is not None and stats_input:
        try:
            yearly: Dict[str, Any] = {}
            latest_margins: Dict[str, float] = {}
            period_spans: List[str] = []
            for item in financial_history:
                if not isinstance(item, dict):
                    continue
                entity = str(item.get("entity", ""))
                rev = (item.get("revenue", {}) or {})
                inc = (item.get("net_income", {}) or {})
                rev_labels = list(rev.get("labels", []) or [])
                rev_values = list(rev.get("values", []) or [])
                inc_labels = list(inc.get("labels", []) or [])
                inc_values = list(inc.get("values", []) or [])
                entry_yearly: Dict[str, Any] = {}
                if rev_values and compute_yearly_stats is not None:
                    entry_yearly["revenue"] = compute_yearly_stats(
                        rev_labels, rev_values
                    )
                if inc_values and compute_yearly_stats is not None:
                    entry_yearly["net_income"] = compute_yearly_stats(
                        inc_labels, inc_values
                    )
                if (
                    rev_values and inc_values
                    and compute_net_margins is not None
                ):
                    margins = compute_net_margins(rev_values, inc_values)
                    entry_yearly["net_margin"] = [
                        {"label": label, "value": margin}
                        for label, margin in zip(rev_labels, margins)
                    ]
                    for margin in reversed(margins):
                        if isinstance(margin, (int, float)):
                            latest_margins[entity] = margin
                            break
                if entry_yearly:
                    yearly[entity] = entry_yearly
                if rev_labels:
                    period_spans.append(
                        f"{entity}: {rev_labels[0]} -> {rev_labels[-1]}"
                    )
            period_basis = (
                "Fiscal-year labels as reported ("
                + ("; ".join(period_spans) if period_spans else "no annual labels")
                + "); growth compares each company's latest vs earliest "
                "completed annual period in-window."
            )
            if gate.get("currency_detail"):
                period_basis += " " + str(gate["currency_detail"])
            gate["comparison_stats"] = compute_comparison_stats(
                stats_input,
                latest_margins or None,
                yearly or None,
                period_basis,
            )
        except Exception as exc:
            logger.warning("Comparison stats failed: %s", exc)
            gate["comparison_stats"] = None
    return gate


def _fail_closed_gate(query: str, reason: str) -> Dict[str, Any]:
    """Blocked gate for when correctness validation itself throws.

    Fail-closed: a validator exception must BLOCK comparison/chart, never
    continue with existing visuals. `applies` is derived from a guarded
    decomposition so non-comparison queries (own-data rows, sentiment
    prose) keep their normal path; comparison queries get applies=True,
    blocked=True with the failure recorded as the blocked reason.
    """
    entities: list = []
    metrics: list = []
    years: Optional[int] = None
    applies = False
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            entities = list(decomposed.get("entities", []) or [])
            metrics = list(decomposed.get("metrics", []) or [])
            years = decomposed.get("period_years")
            applies = bool(decomposed.get("is_comparison")) and len(entities) >= 2
    except Exception:
        applies = False
    return {
        "applies": applies,
        "blocked": True if applies else False,
        "blocked_reason": str(reason or "")[:500],
        "entities": entities,
        "metrics": metrics,
        "years": years,
        "historical_ok": False,
        "historical_detail": str(reason or "")[:300],
        "comparison_ok": False,
        "comparison_detail": str(reason or "")[:300],
        "comparison_stats": None,
        "validation_failed": True,
    }


# ---------------------------------------------------------------------------
# Visual provenance contract + single validated-evidence state + staleness.
# Every visual carries requested intent, entities, metric, units, timeframe,
# frequency, evidence/source IDs, data points, computation IDs. Before
# rendering, the visual's data must match the answer's validated evidence;
# entity/metric/timeframe/units/source/value mismatches or a different
# semantic intent reject the visual (fail closed). What-if queries never
# reuse market-history charts; follow-ups regenerate when entity/metric/
# period/intent changes.
# ---------------------------------------------------------------------------
_WHAT_IF_RE = re.compile(
    r"\bwhat\s+(?:\w+\s+){0,4}if\b|\bscenario\b|\bassume\b.*\b(grow|drop|rise|fall|increase|decrease)",
    re.IGNORECASE,
)


def is_what_if_query(query: str) -> bool:
    """True for what-if / scenario intent (generic, not price-only)."""
    try:
        from app.services.data.stats import parse_what_if as _parse_wif

        if _parse_wif(query or "") is not None:
            return True
    except Exception:
        pass
    if _WHAT_IF_RE.search(query or ""):
        return True
    # Conditional multi-lever scenarios ("If price +25%, lose 18% of
    # customers, ..., calculate the new revenue") don't say "what if" but
    # are the same intent -- the query states its own baseline numbers
    # with no uploaded data needed. Two detection paths: (a) the extractor
    # itself confidently parses a baseline + lever, or (b) a lighter regex
    # backstop for cases the extractor can't cleanly parse a baseline for
    # (still worth routing away from market-history/historical-comparison
    # visuals even when no number can be computed).
    try:
        from app.services.data.stats import (
            compute_freeform_scenario as _compute_freeform,
        )

        if _compute_freeform(query or "") is not None:
            return True
    except Exception:
        pass
    try:
        from app.services.data.stats import CONDITIONAL_SCENARIO_RE as _cond_re

        if _cond_re.search(query or ""):
            return True
    except Exception:
        pass
    return False


def compute_structured_what_if(
    financial_history: Optional[list], query: str
) -> Optional[Dict[str, Any]]:
    """Deterministic what-if scenario from validated financial history.

    Generic across arbitrary entities: extracts a signed percent from the
    query ("grow 10%", "drop 5%", "increase by 12%") and scales each
    entity's latest annual revenue by that factor (quantity-unaffected
    assumption, same as the row-level what-if). Returns a single aggregate
    scenario (baseline_total / scenario_total / delta + per-entity table)
    for narration to quote verbatim -- never LLM arithmetic. None when no
    percent or no revenue history. Pure.
    """
    try:
        text = query or ""
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s?%", text)
        if not pct_match:
            return None
        try:
            pct = float(pct_match.group(1))
        except (TypeError, ValueError):
            return None
        # Direction from surrounding words; bare "change 10%" defaults up.
        span = text[max(0, pct_match.start() - 60):pct_match.end() + 20]
        down = bool(re.search(
            r"\b(lower|drop\w*|decreas\w*|down|cut\w*|reduc\w*|less|fewer|fall\w*|declin\w*)\b",
            span, re.IGNORECASE,
        ))
        up = bool(re.search(
            r"\b(rais\w*|ris\w*|rose|increas\w*|up|higher|hik\w*|more|grow\w*|gain\w*)\b",
            span, re.IGNORECASE,
        ))
        if down and not up:
            pct = -pct
        if pct == 0:
            return None
        factor = 1.0 + pct / 100.0
        if factor <= 0:
            return None
        per_entity: Dict[str, Dict[str, float]] = {}
        baseline_total = 0.0
        for item in (financial_history or []):
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity", item.get("symbol", "")) or "").strip()
            if not entity:
                continue
            chunk = (item.get("revenue", {}) or {})
            vals = list(chunk.get("values") or [])
            if not vals:
                continue
            try:
                latest = float(vals[-1])
            except (TypeError, ValueError):
                continue
            scenario = latest * factor
            per_entity[entity] = {
                "baseline": round(latest, 2),
                "scenario": round(scenario, 2),
                "delta": round(scenario - latest, 2),
            }
            baseline_total += latest
        if not per_entity:
            return None
        scenario_total = round(baseline_total * factor, 2)
        baseline_total = round(baseline_total, 2)
        return {
            "target": "revenue",
            "pct_change": pct,
            "factor": round(factor, 4),
            "baseline_total": baseline_total,
            "scenario_total": scenario_total,
            "delta": round(scenario_total - baseline_total, 2),
            "per_entity": per_entity,
            "basis": "latest annual revenue per entity scaled by the scenario factor",
            "assumption": (
                "Assumes quantity/mix unaffected by the change "
                "(no elasticity modeled)."
            ),
        }
    except Exception as exc:
        logger.warning("Structured what-if failed: %s", exc)
        return None

__all__ = [
    "_WHAT_IF_RE",
    "_fail_closed_gate",
    "_historical_comparison_gate",
    "compute_structured_what_if",
    "is_what_if_query",
]
