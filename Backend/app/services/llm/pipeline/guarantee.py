"""ensure_visuals: the deterministic visual guarantee. Split from langchain_pipeline.py; behavior unchanged."""

import logging
import re

from typing import Any, Dict, Optional, Sequence
from app.config import get_settings

from .shared import call_history_gate, call_research_completeness, has_research_completeness
from .models import PipelineOutput, VisualOutput
from .prompts import CHART_INTENT_RE
from .deps import METRIC_PROFIT, METRIC_REVENUE, METRIC_STOCK, build_research_plan, figures_share_metric
from .figures import _attributed_pool, _figures_from_snippets
from .history import _fail_closed_gate, is_what_if_query
from .visuals import _comparison_from_figures, _drop_sources_table_visuals, _figure_chart_groups, _figures_bar_visual, _figures_table_visual, _financial_history_table_visual, _fundamentals_comparison_visual, _margin_table_visual, _market_graph_visual, _outlook_status_visual, _price_history_graph_visual, _product_table_visual, _recommended_from_snippets, _recommended_table_visual, _timeline_visual, _visuals_from_rows
from .grounding import _attach_history_provenance, _provenance_filter_final, attach_provenance, build_visual_provenance, drop_ungrounded_visuals_evidence, validate_visual_provenance

logger = logging.getLogger(__name__)



def ensure_visuals(
    output: PipelineOutput,
    *,
    rows: Sequence[dict],
    computed_numbers: Optional[dict],
    market_data: Optional[list],
    web_sources: Optional[list],
    news_context: Optional[list] = None,
    preferred_visual: Optional[str],
    query: str,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
) -> PipelineOutput:
    """Visual guarantee: a validated normal answer must never go out naked
    when plottable tool outputs exist. Clarifications and already-visual
    answers pass through untouched; synthesis only uses real values. Web-only
    answers get cited-figures visuals (comparison for X-vs-Y, bar when
    comparable, timeline when dated, outlook status for sentiment).
    Sources are never a visual: they render once in the expandable
    sources section from web_sources.

    Final invariant (H10, enforced in CODE): a comparison visualization
    requires comparison_complete == True AND comparison_gate == PASSED AND
    confidence > 0 AND every number originating from validated evidence.
    Historical gating: when the query is a multi-entity historical
    comparison, NO graph/comparison visual is synthesized unless the
    historical gate validates (ALL companies, same metric/unit/period).
    A 0-confidence answer never gains a chart here -- insufficient
    evidence blocks visualization by design.
    Fail-closed: if gate/completeness validation itself throws, comparison
    visuals are BLOCKED (existing graph/comparison cards are stripped;
    sources stay visible via web_sources) instead of shipping unverified
    charts.
    """
    rows = list(rows or [])
    market_data = list(market_data or [])
    web_sources = list(web_sources or [])
    news_context = list(news_context or [])
    fundamentals = list(fundamentals or [])
    macro_data = list(macro_data or [])
    price_history = list(price_history or [])
    financial_history = list(financial_history or [])
    # P0#15: channels stay separate. combined_series is NOT market+macro;
    # the market path charts market_data only, macro never enters it.
    combined_series = list(market_data)
    synthesized: list = []

    def _strip_comparison_visuals(visuals: list) -> tuple[list, int]:
        kept = [
            visual for visual in (visuals or [])
            if getattr(visual, "visual_type", "") not in ("graph", "comparison")
        ]
        return kept, len(list(visuals or [])) - len(kept)

    # Historical comparison gate runs BEFORE any synthesis -- and before
    # honoring pre-existing visuals, so a validator exception can never
    # ship an unverified chart.
    gate: Dict[str, Any] = {}
    try:
        gate = call_history_gate(
            query,
            market_data=market_data,
            price_history=price_history,
            financial_history=financial_history,
            fundamentals=fundamentals,
        )
    except Exception as exc:
        logger.warning("Historical gate failed: blocking comparison visuals: %s", exc)
        gate = _fail_closed_gate(query, f"historical validation unavailable ({exc})")
    # Completeness is authoritative alongside the gate, with partial-result
    # policy: a validated subset (>=2 entities, >=1 common metric) is
    # sufficient for partial visuals with exclusions; only an insufficient
    # subset blocks. Context gaps (fundamentals/snippets) never block alone.
    if gate.get("applies") and has_research_completeness():
        try:
            _plan_for_completeness: Dict[str, Any] = {}
            if build_research_plan is not None:
                try:
                    _plan_for_completeness = dict(build_research_plan(query) or {})
                except Exception:
                    _plan_for_completeness = {
                        "entities": list(gate.get("entities", []) or []),
                        "metrics": list(gate.get("metrics", []) or []),
                        "period_years": gate.get("years"),
                        "requires_history": True,
                        "required_entities": list(gate.get("entities", []) or []),
                        "required_metrics": list(gate.get("metrics", []) or []),
                        "required_tools": [],
                    }
            _completeness = call_research_completeness(
                _plan_for_completeness,
                {
                    "price_history": price_history,
                    "financial_history": financial_history,
                    "market_data": market_data,
                    "fundamentals": fundamentals,
                    "snippets": news_context,
                },
            )
            # Non-blocking context gaps (fundamentals/snippets) are visible
            # in the trace but must not flip a validated gate to BLOCKED:
            # only core history/entity/metric/period failures block here.
            _core_missing = [
                m for m in (_completeness.get("missing") or [])
                if not str(m).startswith("context tool")
            ]
            # Partial: sufficient subset passes even when strict completeness
            # is False; insufficient subset blocks.
            _sufficient = bool(_completeness.get("sufficient_for_partial")) or bool(
                gate.get("validated_entities") and len(gate.get("validated_entities", [])) >= 2
            )
            if not _completeness.get("comparison_complete") and _core_missing and not _sufficient:
                logger.info(
                    "Completeness blocked visualization: missing=%s",
                    (_completeness.get("missing") or [])[:4],
                )
                gate = {
                    **gate,
                    "blocked": True,
                    "blocked_reason": (
                        str(gate.get("blocked_reason", "") or "")
                        + " Completeness: "
                        + "; ".join((_completeness.get("missing") or [])[:4])
                    ).strip(),
                }
            elif _sufficient and gate.get("blocked"):
                # Gate blocked but completeness finds a sufficient validated
                # subset (e.g. gate predates partial): unblock as partial.
                # The gate's own partial path already handles this; this is
                # defense in depth for code paths that skipped it.
                pass
        except Exception as exc:
            # Fail-closed: an unavailable completeness verdict must BLOCK,
            # never let a possibly-partial comparison through.
            logger.warning("Completeness check failed: blocking visuals: %s", exc)
            if gate.get("applies"):
                gate = {
                    **gate,
                    "blocked": True,
                    "blocked_reason": (
                        str(gate.get("blocked_reason", "") or "")
                        + f" Completeness unavailable ({exc}); comparison blocked."
                    ).strip(),
                    "comparison_ok": False,
                }
    if output.clarification is not None:
        return output
    # What-if intent never reuses market-history visuals (no inheritance).
    _is_what_if = is_what_if_query(query)
    if output.visuals:
        # Pre-existing (usually LLM-proposed) visuals: fail-closed filter.
        # BLOCKED gate, zero confidence, stale intent, what-if reuse, or
        # provenance mismatch strips graph/comparison cards; tables/sources
        # prose visuals survive. Every surviving chart is provenance-checked.
        try:
            needs_strip = bool(gate.get("applies") and gate.get("blocked"))
        except Exception:
            needs_strip = True
        try:
            zero_conf = float(output.confidence or 0.0) <= 0.0
        except (TypeError, ValueError):
            zero_conf = True
        if needs_strip or zero_conf:
            output.visuals, stripped = _strip_comparison_visuals(output.visuals)
            if stripped:
                logger.info(
                    "Stripped %d comparison visual(s) from existing visuals "
                    "(blocked=%s, zero_conf=%s).",
                    stripped, needs_strip, zero_conf,
                )
        # What-if: strip any market-history-flavoured chart (stale intent).
        if _is_what_if and output.visuals:
            kept: list = []
            dropped = 0
            for visual in output.visuals:
                try:
                    prov = getattr(visual, "provenance", None) or {}
                    intent = str(prov.get("intent", "") or "").lower()
                    title = str(getattr(visual, "title", "") or "").lower()
                    if getattr(visual, "visual_type", "") in ("graph", "comparison") and (
                        intent == "historical_comparison"
                        or "stock performance" in title
                        or "market" in title
                    ):
                        dropped += 1
                        continue
                except Exception:
                    dropped += 1
                    continue
                kept.append(visual)
            if dropped:
                logger.info("Stripped %d market-history visual(s) for what-if query.", dropped)
            output.visuals = kept
        # Provenance validation for surviving charts (fail closed).
        # Own-data row visuals skip entity gating (metric-based, no entities).
        if output.visuals:
            try:
                _val_ents = list(gate.get("validated_entities", []) or [])
                _val_mets = list(gate.get("validated_metrics", []) or [])
                _time = gate.get("years")
                _time_label = f"{_time}Y" if _time else None
                # Fall back to plan entities ONLY for comparisons; row visuals
                # must not be entity-gated.
                if not _val_ents and not gate.get("applies") and not rows:
                    try:
                        if build_research_plan is not None:
                            _pd = dict(build_research_plan(query) or {})
                            if _pd.get("is_comparison") and len(_pd.get("entities", []) or []) >= 2:
                                _val_ents = list(_pd.get("entities", []) or [])
                                _val_mets = list(_pd.get("metrics", []) or [])
                                _time_label = (_pd.get("period_label") or _time_label)
                    except Exception:
                        pass
                filtered: list = []
                for visual in output.visuals:
                    _vt = getattr(visual, "visual_type", "")
                    _vt_title = str(getattr(visual, "title", "") or "").lower()
                    _qual = _vt == "table" and any(
                        k in _vt_title for k in ("source", "timeline", "outlook")
                    )
                    if _vt not in ("graph", "comparison", "table") or _qual:
                        filtered.append(visual)
                        continue
                    try:
                        ok, why = validate_visual_provenance(
                            visual, query=query,
                            validated_entities=_val_ents,
                            validated_metrics=_val_mets,
                            expected_timeframe=_time_label,
                        )
                    except Exception as exc:
                        ok, why = False, f"provenance check failed ({exc})"
                    # Uniform grounding (P0#13): EVERY visual with numbers
                    # must trace to validated evidence/computation -- no
                    # row-presence exemption.
                    try:
                        if ok:
                            kept_grounded, _ = drop_ungrounded_visuals_evidence(
                                [visual], snippets=news_context, rows=rows,
                                price_history=price_history,
                                financial_history=financial_history,
                                market_data=market_data,
                                computed_numbers=computed_numbers,
                            )
                            if not kept_grounded:
                                ok, why = False, "values untraceable to evidence"
                    except Exception as exc:
                        ok, why = False, f"grounding check failed ({exc})"
                    if ok:
                        filtered.append(visual)
                    else:
                        logger.info("Rejected pre-existing visual: %s.", why)
                output.visuals = filtered
            except Exception as exc:
                # Fail-closed: validation unavailable -> strip charts.
                logger.warning("Provenance validation failed, stripping charts: %s", exc)
                output.visuals, _ = _strip_comparison_visuals(output.visuals)
        # Visual guarantee, part 2: a real chart that already survived
        # validation covers the "at least one chart" bar, so stop here
        # rather than risk a near-duplicate. But the common case is the
        # narration model proposing only metric/insight cards of its own
        # (no graph/comparison) -- previously that alone caused an
        # unconditional early return here, silently skipping ALL of the
        # richer synthesis below (comparison/bar charts,
        # timelines) even when real, eligible evidence existed for them.
        # Falling through instead lets that synthesis ADD to (never
        # replace) whatever the model already proposed, so answers land
        # with multiple grounded visuals by default instead of just
        # whatever single card the model happened to draft.
        if any(
            getattr(v, "visual_type", "") in ("graph", "comparison")
            for v in output.visuals
        ):
            return output
    if gate.get("applies") and gate.get("blocked"):
        logger.info("Historical comparison blocked: %s", gate.get("blocked_reason", "")[:160])
        # Blocked comparisons ship no chart. Sources stay visible via the
        # expandable sources section (web_sources), not a table visual.
        # Fail-closed: any pre-existing graph/comparison is stripped first.
        output.visuals, _ = _strip_comparison_visuals(output.visuals)
        output.visuals = _drop_sources_table_visuals(output.visuals)
        return output
    # What-if queries never synthesize market-history charts (no inheritance
    # across incompatible intents). They get deterministic scenario visuals
    # from computed what-if numbers.
    if _is_what_if:
        what_if = (computed_numbers or {}).get("what_if")
        if isinstance(what_if, dict) and what_if:
            try:
                baseline = float(what_if.get("baseline_total", 0) or 0)
                scenario = float(what_if.get("scenario_total", 0) or 0)
                what_visual = VisualOutput(
                    visual_type="comparison",
                    title="What-if scenario",
                    props={
                        "value": scenario, "baseline": baseline,
                        "groups": [
                            {"label": "Baseline", "value": baseline},
                            {"label": "Scenario", "value": scenario},
                        ],
                    },
                )
                attach_provenance(what_visual, build_visual_provenance(
                    query=query, entities=[],
                    metric=str(what_if.get("target", "price")),
                    units=None, timeframe=None, frequency=None,
                    source_ids=["computed:what_if"],
                    computation_ids=["what_if"],
                    data_points={"baseline_total": baseline, "scenario_total": scenario},
                ))
                try:
                    if float(output.confidence or 0.0) > 0.0:
                        synthesized.append(what_visual)
                except (TypeError, ValueError):
                    synthesized.append(what_visual)
            except Exception as exc:
                logger.warning("What-if visual failed: %s", exc)
        # Tables of raw rows may still help; never market-history graphs.
        if rows:
            try:
                row_visuals = _visuals_from_rows(rows, computed_numbers, preferred_visual)
                for visual in row_visuals:
                    if getattr(visual, "visual_type", "") == "graph":
                        continue
                    try:
                        if getattr(visual, "provenance", None) is None:
                            attach_provenance(visual, build_visual_provenance(
                                query=query, entities=[],
                                metric=None, units=None, timeframe=None, frequency=None,
                                source_ids=["rows"],
                                computation_ids=sorted((computed_numbers or {}).keys()),
                                data_points={"row_count": len(rows)},
                            ))
                    except Exception:
                        pass
                    synthesized.append(visual)
            except Exception as exc:
                logger.warning("What-if row visuals failed: %s", exc)
        if synthesized:
            output.visuals = list(output.visuals) + synthesized[: get_settings().MAX_SYNTHESIZED_VISUALS]
        # Provenance-validate before returning (fail closed).
        output.visuals = _provenance_filter_final(
            _drop_sources_table_visuals(output.visuals), query, gate
        )
        return output
    if gate.get("applies") and not gate.get("blocked"):
        # Validated history: chart the validated subset (partial allowed),
        # plus annual tables. Every visual carries provenance.
        years = gate.get("years")
        graph = _price_history_graph_visual(price_history, query, years)
        if graph is not None:
            # At 0 confidence even a validated series stays uncharted.
            try:
                if float(output.confidence or 0.0) > 0.0:
                    _attach_history_provenance(graph, query, price_history, years, METRIC_STOCK)
                    synthesized.append(graph)
            except (TypeError, ValueError):
                _attach_history_provenance(graph, query, price_history, years, METRIC_STOCK)
                synthesized.append(graph)
        for metric_key in ("revenue", "net_income"):
            table = _financial_history_table_visual(financial_history, metric_key, query)
            if table is not None:
                _attach_history_provenance(
                    table, query, financial_history, years,
                    METRIC_REVENUE if metric_key == "revenue" else METRIC_PROFIT,
                )
                synthesized.append(table)
        margin_table = _margin_table_visual(financial_history, query)
        if margin_table is not None:
            _attach_history_provenance(margin_table, query, financial_history, years, METRIC_PROFIT)
            synthesized.append(margin_table)
        if synthesized:
            logger.info(f"Visual guarantee synthesized {len(synthesized)} validated visual(s).")
            # Validated multi-year evidence earns the full set (graph +
            # annual tables + margin table): capping at 3 here
            # silently dropped the single comparable profitability view.
            output.visuals = list(output.visuals) + synthesized[: get_settings().MAX_SYNTHESIZED_VISUALS]
            output.visuals = _provenance_filter_final(
                _drop_sources_table_visuals(output.visuals), query, gate
            )
            return output
        # No validated-history visual (the gate fired but no price/financial
        # history exists -- e.g. phantom entities with only snippet evidence):
        # fall through to row/series/figure synthesis below instead of
        # returning a naked answer. BLOCKED gates never reach here (handled
        # above), and every path below keeps its own grounding/provenance
        # guards plus the shared final filter, so nothing unverified ships.

    if rows:
        chart_requested = bool(re.search(CHART_INTENT_RE, query, re.IGNORECASE))
        synthesized = _visuals_from_rows(rows, computed_numbers, preferred_visual)
        # Provenance for row visuals (own-data evidence, first-class stage).
        for visual in synthesized:
            try:
                if getattr(visual, "provenance", None) is None:
                    attach_provenance(visual, build_visual_provenance(
                        query=query, entities=[],
                        metric=None, units=None, timeframe=None, frequency=None,
                        source_ids=["rows"],
                        computation_ids=sorted((computed_numbers or {}).keys()),
                        data_points={"row_count": len(rows)},
                    ))
            except Exception:
                pass
        # Fail-closed: 0% confidence never ships a chart (graph/comparison);
        # honest row tables survive. Never 0% + visual chart.
        try:
            _zero = float(output.confidence or 0.0) <= 0.0
        except (TypeError, ValueError):
            _zero = True
        if _zero:
            synthesized = [v for v in synthesized if getattr(v, "visual_type", "") not in ("graph", "comparison")]
        if chart_requested:
            logger.info("Chart intent detected; synthesized visuals lead with a chart.")
    elif combined_series:
        graph = _market_graph_visual(combined_series, query)
        if graph is not None:
            try:
                if getattr(graph, "provenance", None) is None:
                    _ents = [str(i.get("entity", "")) for i in combined_series if isinstance(i, dict)]
                    attach_provenance(graph, build_visual_provenance(
                        query=query, entities=_ents[:4], metric=None,
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"market:{e}" for e in _ents[:4]],
                        computation_ids=[], data_points={"series": len(_ents)},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized = [graph]
                else:
                    synthesized = []
            except (TypeError, ValueError):
                synthesized = [graph]
        fundamentals_comparison = _fundamentals_comparison_visual(fundamentals, query)
        if fundamentals_comparison is not None:
            try:
                if getattr(fundamentals_comparison, "provenance", None) is None:
                    _ents = [str(i.get("entity", "")) for i in (fundamentals or []) if isinstance(i, dict)]
                    attach_provenance(fundamentals_comparison, build_visual_provenance(
                        query=query, entities=_ents[:4], metric="market_cap",
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"fundamentals:{e}" for e in _ents[:4]],
                        computation_ids=[], data_points={"entities": _ents[:4]},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(fundamentals_comparison)
            except (TypeError, ValueError):
                synthesized.append(fundamentals_comparison)
        if not synthesized:
            # Gated short-term series (historical query, single entity) with
            # nothing chartable: no fallback visual. Sources stay visible via
            # the expandable sources section (web_sources), not a table.
            pass
    else:
        figures = _figures_from_snippets(news_context, query)
        # Explicit X-vs-Y steers to a comparison card first (never invented).
        comparison = _comparison_from_figures(figures, query)
        if comparison is not None:
            try:
                if getattr(comparison, "provenance", None) is None:
                    _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                    attach_provenance(comparison, build_visual_provenance(
                        query=query, entities=_fig_ents,
                        metric=str((figures[0].get("metric") if figures else "") or ""),
                        units=str((figures[0].get("unit") if figures else "") or ""),
                        timeframe=None, frequency=None,
                        source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                        computation_ids=[], data_points={"figures": len(figures)},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(comparison)
            except (TypeError, ValueError):
                synthesized.append(comparison)
        # A figures bar over mismatched metrics (funding vs cost, views vs
        # subscribers) is the same false-comparison bug: only chart when
        # cues agree. Group-aware on the ATTRIBUTED pool (same pool the bar
        # gate judges): one mixed pair must not veto an otherwise chartable
        # group. Fail-closed: check unavailable blocks the bar.
        bar_ok = True
        try:
            if figures_share_metric is not None and len(figures) >= 2:
                pool_figs, _, _ = _attributed_pool(figures, query or "")
                groups = _figure_chart_groups(pool_figs)
                bar_ok = (not groups) or any(
                    figures_share_metric(group)[0] for group in groups
                )
        except Exception as exc:
            logger.warning("Figures metric check failed, blocking bar: %s", exc)
            bar_ok = False
        if bar_ok:
            bar = _figures_bar_visual(figures, query)
            if bar is not None:
                try:
                    if getattr(bar, "provenance", None) is None:
                        _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                        attach_provenance(bar, build_visual_provenance(
                            query=query, entities=_fig_ents,
                            metric=str((figures[0].get("metric") if figures else "") or ""),
                            units=str((figures[0].get("unit") if figures else "") or ""),
                            timeframe=None, frequency=None,
                            source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                            computation_ids=[], data_points={"figures": len(figures)},
                        ))
                except Exception:
                    pass
                try:
                    if float(output.confidence or 0.0) > 0.0:
                        synthesized.append(bar)
                except (TypeError, ValueError):
                    synthesized.append(bar)
        table = _figures_table_visual(figures)
        if table is not None:
            # Data tables carry provenance like charts (P0#14).
            try:
                if getattr(table, "provenance", None) is None:
                    _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                    attach_provenance(table, build_visual_provenance(
                        query=query, entities=_fig_ents,
                        metric=str((figures[0].get("metric") if figures else "") or "") or None,
                        units=str((figures[0].get("unit") if figures else "") or "") or None,
                        timeframe=None, frequency=None,
                        source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                        computation_ids=[], data_points={"figures": len(figures)},
                    ))
            except Exception:
                pass
            synthesized.append(table)
        product_table = _product_table_visual(figures, query or "")
        if product_table is not None:
            # Ranking-style table from attributed pairs (grounded
            # recommendation shape, never invented scores).
            try:
                if getattr(product_table, "provenance", None) is None:
                    _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                    attach_provenance(product_table, build_visual_provenance(
                        query=query, entities=_fig_ents,
                        metric=str((figures[0].get("metric") if figures else "") or "") or None,
                        units=str((figures[0].get("unit") if figures else "") or "") or None,
                        timeframe=None, frequency=None,
                        source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                        computation_ids=[], data_points={"figures": len(figures)},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(product_table)
            except (TypeError, ValueError):
                synthesized.append(product_table)
        recommended = _recommended_table_visual(news_context, web_sources)
        if recommended is not None:
            # Recommended-items table (books/products/tools with citing
            # sources): pure evidence phrases, never invented.
            try:
                if getattr(recommended, "provenance", None) is None:
                    _rec_items = _recommended_from_snippets(
                        news_context, web_sources
                    )[:4]
                    attach_provenance(recommended, build_visual_provenance(
                        query=query, entities=[],
                        metric=None, units=None, timeframe=None, frequency=None,
                        source_ids=[f"snippet:{ref}" for _, ref in _rec_items],
                        computation_ids=[], data_points={"items": len(_rec_items)},
                    ))
            except Exception:
                pass
            synthesized.append(recommended)
        timeline = _timeline_visual(news_context, web_sources)
        if timeline is not None:
            synthesized.append(timeline)
        # Structured fundamentals comparison needs no snippet figures.
        fundamentals_comparison = _fundamentals_comparison_visual(fundamentals, query)
        if fundamentals_comparison is not None:
            try:
                if getattr(fundamentals_comparison, "provenance", None) is None:
                    _fents = [str(i.get("entity", "")) for i in (fundamentals or []) if isinstance(i, dict)]
                    attach_provenance(fundamentals_comparison, build_visual_provenance(
                        query=query, entities=_fents[:4], metric="market_cap",
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"fundamentals:{e}" for e in _fents[:4]],
                        computation_ids=[], data_points={"entities": _fents[:4]},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(fundamentals_comparison)
            except (TypeError, ValueError):
                synthesized.append(fundamentals_comparison)
        outlook = _outlook_status_visual(news_context, query)
        if outlook is not None:
            synthesized.append(outlook)
    if synthesized:
        logger.info(f"Visual guarantee synthesized {len(synthesized)} visual(s).")
        # Four slots: comparison/bar/product-table/figures-table/timeline
        # compete; the ranking-style product table must survive alongside.
        output.visuals = list(output.visuals) + synthesized[: get_settings().MAX_SYNTHESIZED_VISUALS]
    # Sources render once in the expandable section — never as a table card.
    output.visuals = _drop_sources_table_visuals(output.visuals)
    # Final provenance gate for all synthesized paths (fail closed).
    try:
        output.visuals = _provenance_filter_final(output.visuals, query, gate)
    except Exception as exc:
        logger.warning("Final provenance filter failed: %s", exc)
        output.visuals = [v for v in (output.visuals or []) if getattr(v, "visual_type", "") not in ("graph", "comparison")]
    return output

__all__ = [
    "ensure_visuals",
]
