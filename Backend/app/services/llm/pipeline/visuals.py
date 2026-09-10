"""Deterministic visual builders (one per component). Split from langchain_pipeline.py; behavior unchanged."""

import logging
import re

from typing import Any, Dict, Optional, Sequence

from .models import VisualOutput
from .prompts import CHART_INTENT_RE, COMPARISON_INTENT_RE, SENTIMENT_INTENT_RE, _NEGATIVE_WORDS, _POSITIVE_WORDS
from .deps import compute_net_margins, decompose_comparison_query, figure_entity_label, figures_share_metric, is_comparison_query, validate_historical_coverage
from .figures import _RECOMMENDED_BY_RE, _RECOMMENDED_QUOTE_RE, _SOURCES_TABLE_COLUMNS, _SOURCES_TABLE_TITLES, _SYNTH_FINANCIAL_MAX_ROWS, _SYNTH_ITEMS_MAX, _SYNTH_SERIES_MAX_POINTS, _SYNTH_SOURCES_MAX_ROWS, _SYNTH_TABLE_MAX_COLS, _SYNTH_TABLE_MAX_ROWS, _apply_budget_constraint, _attributed_pool, _figure_label, _interleave_entities
from .history import is_what_if_query

logger = logging.getLogger(__name__)



def _figures_table_visual(figures: list) -> Optional[VisualOutput]:
    if not figures:
        return None
    return VisualOutput(
        visual_type="table",
        title="Figures cited",
        props={
            "columns": ["Figure", "Context"],
            "values": [
                [f"{figure['text']} [{figure['ref']}]", figure["context"]]
                for figure in figures
            ],
        },
    )


def _figure_chart_group_key(figure: dict):
    """Chart-grouping key: money/percent pool by unit (legacy shape);
    counts split by metric so views never bar against subscribers."""
    unit = (figure or {}).get("unit")
    if unit in ("money", "percent"):
        return unit
    return (unit, (figure or {}).get("metric") or (figure or {}).get("count_noun") or "")


def _figure_chart_groups(figures: list) -> list:
    """Candidate chart groups: 2+ figures sharing one grouping key."""
    by_key: dict = {}
    for figure in figures or []:
        by_key.setdefault(_figure_chart_group_key(figure), []).append(figure)
    return [group for group in by_key.values() if len(group) >= 2]


def _comparison_intent_pool(figures: list, query: Optional[str] = None) -> Optional[list]:
    """Eligible-only pool for comparison-intent queries ("best", "vs",
    "compare"): ONLY strictly bound figures (H2) may chart, and money in
    KNOWN different currencies never mixes. Full pool otherwise. None when
    the gate blocks. Shared by the bar and the product table so both show
    the same member set."""
    pool = list(figures or [])
    try:
        if query and is_comparison_query is not None and is_comparison_query(query):
            eligible = [fig for fig in pool if fig.get("comparison_eligible")]
            if len(eligible) < 2:
                logger.info(
                    "Chart pool blocked: fewer than 2 semantically bound "
                    "figures for a comparison query."
                )
                return None
            pool = eligible
            # H5: money bars in KNOWN different currencies never compare.
            try:
                known = {
                    str(fig.get("currency", "") or "").strip().upper()
                    for fig in pool if fig.get("unit") == "money"
                }
                known.discard("")
                if len(known) > 1:
                    logger.info(
                        "Chart pool blocked: mixed currencies %s.", sorted(known)
                    )
                    return None
            except Exception as exc:
                # Fail-closed: currency check unavailable -> block.
                logger.warning("Figure currency check failed, blocking: %s", exc)
                return None
    except Exception as exc:
        # Fail-closed: eligibility check unavailable -> block.
        logger.warning("Figure eligibility check failed, blocking: %s", exc)
        return None
    return pool


def _figures_bar_visual(
    figures: list, query: Optional[str] = None
) -> Optional[VisualOutput]:
    """Bar chart over same-class figures (money with money, percent with
    percent, counts with same-metric counts) using normalized values;
    labels carry citation numbers.

    Contract (Phase 6/13, hardened H2/H3): on an explicit comparison query
    ONLY strictly eligible figures (H2 contract) may chart -- untyped
    figures cannot become comparison evidence -- AND figures with
    EXPLICITLY different metric cues (funding vs startup_cost, revenue vs
    cost, views vs subscribers) never share one bar (the 504999900% root
    cause). Qualitative (non-comparison) queries are lenient on eligibility
    (untyped figures may chart) but NEVER on attribution: every bar names
    its resolved entity, over-budget prices are excluded, and conflicting
    metrics never share one bar.
    """
    pool = _comparison_intent_pool(figures, query)
    if pool is None:
        return None
    # Attributed-datum gate: every bar must name its WHO (resolved entity).
    # Unattributed figures stay citable in the figures table only.
    pool_figs, entities_by_id, _ = _attributed_pool(pool, query or "")
    if len(pool_figs) < 2:
        logger.info("Figures bar blocked: fewer than 2 attributed figures.")
        return None
    picked = _pick_chart_group(pool_figs)
    if picked is None:
        logger.info("Figures bar blocked: no metric-agreeing group.")
        return None
    group, _ = picked
    assert group is not None
    unit = group[0]["unit"]
    if unit == "money":
        unit_word, series_name = "amount", "amount"
    elif unit == "percent":
        unit_word, series_name = "percent", "percent"
    else:
        unit_word = "count"
        series_name = str(group[0].get("metric") or "count")
    return VisualOutput(
        visual_type="graph",
        title=f"Cited {unit_word}s compared",
        props={
            "chart_type": "bar",
            "labels": [
                f"{entities_by_id.get(id(figure), _figure_label(figure))} [{figure['ref']}]"
                for figure in group
            ],
            "datasets": [
                {
                    "name": series_name,
                    "values": [figure["value"] for figure in group],
                }
            ],
        },
    )


def _pick_chart_group(pool_figs: list) -> Optional[tuple]:
    """Largest metric-agreeing chart group (H3): a group with conflicting
    cues is skipped rather than charted. Returns (group, None) or None.
    Shared by the bar chart and the product table so both show the same
    coherent member set."""
    candidates = _figure_chart_groups(pool_figs)
    if not candidates:
        return None
    ordered = sorted(candidates, key=len, reverse=True)
    if figures_share_metric is not None:
        for candidate in ordered:
            try:
                shares, _ = figures_share_metric(candidate)
            except Exception:
                shares = True
            if shares:
                return (candidate, None)
        return None
    return (ordered[0], None)


def _product_table_visual(figures: list, query: str) -> Optional[VisualOutput]:
    """Ranking-style table from attributed chart pairs: Product|Price for
    money, Item|<Metric> for counts — the grounded equivalent of a
    recommendation ranking. Same gated member set as the bar would chart
    (comparison intent needs bound figures; budget violators excluded).
    None when fewer than 2 qualify."""
    pool = _comparison_intent_pool(figures, query)
    if pool is None:
        return None
    pool_figs, entities_by_id, _ = _attributed_pool(pool, query or "")
    if len(pool_figs) < 2:
        return None
    picked = _pick_chart_group(pool_figs)
    if picked is None:
        return None
    group, _ = picked
    unit = group[0].get("unit")
    if unit == "money":
        title, columns = "Products compared", ["Product", "Price"]
    else:
        metric = str(group[0].get("metric") or unit or "value").capitalize()
        title, columns = f"{metric} compared", ["Item", metric]
    return VisualOutput(
        visual_type="table",
        title=title,
        props={
            "columns": columns,
            "values": [
                [
                    entities_by_id.get(id(figure), _figure_label(figure)),
                    f"{figure['text']} [{figure['ref']}]",
                ]
                for figure in group
            ],
        },
    )


def _recommended_from_snippets(
    news_context: Optional[list], web_sources: Optional[list] = None
) -> list:
    """Recommended items as (item, ref) pairs in snippet order, deduped.
    Pure evidence phrases — never invented, never reworded."""
    found: list = []
    seen: set = set()
    for index, snippet in enumerate(news_context or [], start=1):
        text = str(snippet or "")
        candidates: list = []
        for match in _RECOMMENDED_QUOTE_RE.finditer(text):
            candidates.append(match.group(1).strip())
        for match in _RECOMMENDED_BY_RE.finditer(text):
            candidates.append(
                f"{match.group(1).strip()} by {match.group(2).strip()}"
            )
        for item in candidates:
            item = " ".join(item.split())
            if len(item) < 8 or not re.match(r"[A-Z0-9]", item):
                continue
            if not re.search(r"[A-Za-z]", item):
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append((item, index))
            if len(found) >= _SYNTH_ITEMS_MAX:
                return found
    return found


def _recommended_table_visual(
    news_context: Optional[list], web_sources: Optional[list] = None
) -> Optional[VisualOutput]:
    """Ranking-style table of recommended items (books/products/tools) with
    the citing source per row. Needs 2+ distinct items; None otherwise."""
    items = _recommended_from_snippets(news_context, web_sources)
    if len(items) < 2:
        return None
    sources = list(web_sources or [])
    rows = []
    for item, ref in items:
        title = ""
        if 0 <= ref - 1 < len(sources):
            title = str((sources[ref - 1] or {}).get("title", "") or "")[:60]
        rows.append([item, f"{title} [{ref}]" if title else f"[{ref}]"])
    return VisualOutput(
        visual_type="table",
        title="Recommended",
        props={"columns": ["Item", "Source"], "values": rows},
    )


def _drop_sources_table_visuals(visuals: list) -> list:
    """Strip sources-dump table visuals, whoever proposed them.

    Sources render exactly once in the frontend's expandable sources
    section (from web_sources). A "Sources cited" table card next to it is
    pure duplication, so it never ships — whether the synthesis code or the
    model drafted it (prompts used to ask for one; they no longer do).
    Anything that is not clearly a sources dump passes through untouched.
    """
    kept = []
    for visual in visuals or []:
        try:
            title = str(getattr(visual, "title", "") or "").strip().lower()
            if getattr(visual, "visual_type", "") != "table":
                kept.append(visual)
                continue
            props = getattr(visual, "props", {}) or {}
            columns = [str(c).strip().lower() for c in (props.get("columns") or [])]
            if title in _SOURCES_TABLE_TITLES and all(
                c in _SOURCES_TABLE_COLUMNS for c in columns
            ):
                logger.info("Dropped duplicate sources-table visual: %s.", title)
                continue
        except Exception:
            pass
        kept.append(visual)
    return kept


def _timeline_visual(
    news_context: list, web_sources: list
) -> Optional[VisualOutput]:
    """Dated timeline for 'what's going on with X': one row per dated snippet
    (date + trimmed event text), newest first. Needs per-snippet dates (#2);
    returns None when fewer than 2 dated snippets exist."""
    rows: list[list[str]] = []
    for index, snippet in enumerate(news_context or []):
        if index >= len(web_sources or []):
            break
        date = ((web_sources[index] or {}).get("published_date") or "").strip()
        if not date:
            continue
        # Keep ISO dates compact (YYYY-MM-DD); keep human dates like
        # "Wed, 09 Sep 2026" intact instead of blind [:10] truncation
        # ("Wed, 09 Se"). Cap at 24 chars for chip layout.
        short_date = date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", date) else date[:24]
        event = " ".join(str(snippet).split())[:120]
        rows.append([short_date, f"{event} [{index + 1}]"])
    if len(rows) < 2:
        return None
    rows = sorted(rows, key=lambda row: row[0], reverse=True)[
        :_SYNTH_SOURCES_MAX_ROWS
    ]
    return VisualOutput(
        visual_type="table",
        title="Timeline",
        props={"columns": ["Date", "Event"], "values": rows},
    )


def _comparison_from_figures(
    figures: list, query: str
) -> Optional[VisualOutput]:
    """Comparison card for explicit 'X vs Y' web questions: first two
    same-class figures become value/baseline, all become labeled groups.
    Only fires on comparative intent with 2+ comparable figures.

    Hardened against the funding-vs-cost false comparison (shared root
    cause of the tech-startup vs robotics-startup and NVIDIA vs AMD
    failures): figures with EXPLICITLY different metric cues (funding vs
    cost, revenue vs cost, ...) never compare, and -- when the query names
    its entities -- each figure must mention one of them so unrelated
    figures cannot enter the comparison.
    """
    if not COMPARISON_INTENT_RE.search(query or ""):
        return None
    # Phase 4/5 contract: raw untyped figures never enter numerical
    # comparison. Only semantically bound figures (entity AND metric known)
    # are eligible; the rest stay citable prose in the figures table.
    pool = [fig for fig in (figures or []) if fig.get("comparison_eligible")]
    if len(pool) < 2:
        if figures:
            logger.info(
                "Comparison blocked: fewer than 2 semantically bound figures "
                "(untyped figures cannot become comparison evidence)."
            )
        return None
    # Phase 11: money figures in KNOWN different currencies never compare.
    try:
        known_currencies = {
            str(fig.get("currency", "") or "").strip().upper() for fig in pool
            if fig.get("unit") == "money"
        }
        known_currencies.discard("")
        if len(known_currencies) > 1:
            logger.info(
                "Comparison blocked: mixed figure currencies %s.",
                sorted(known_currencies),
            )
            return None
    except Exception as exc:
        # Fail-closed: currency check unavailable -> block comparison.
        logger.warning("Figure currency check failed, blocking comparison: %s", exc)
        return None
    # Budget: figures violating the query's stated budget never compare as
    # answers to "under X" (they stay citable in the figures table).
    pool = _apply_budget_constraint(pool, query or "")
    if len(pool) < 2:
        logger.info("Comparison blocked: fewer than 2 figures within budget.")
        return None
    candidates = _figure_chart_groups(pool)
    if not candidates:
        return None
    # Prefer the largest group, but drop any group whose figures
    # carry conflicting metric cues (total funding vs startup cost, ...).
    ordered = sorted(candidates, key=len, reverse=True)
    group: Optional[list] = None
    if figures_share_metric is not None:
        for candidate in ordered:
            try:
                shares, _ = figures_share_metric(candidate)
            except Exception:
                # Fail-closed: metric-agreement check unavailable -> skip group.
                logger.warning("Metric-agreement check failed, skipping group.")
                continue
            if shares:
                group = candidate[:6]
                break
        if group is None:
            logger.info("Comparison blocked: figures carry mismatched metrics.")
            return None
    else:
        group = ordered[0][:6]
    # Entity attribution: when the query names comparison entities, every
    # figure used must mention one of them. Unrelated money figures (random
    # snippet values that never name NVIDIA/AMD, tech/robotics, ...) cannot
    # form a "comparison".
    try:
        entities: list = []
        if decompose_comparison_query is not None:
            entities = (decompose_comparison_query(query or "") or {}).get("entities", []) or []
        if entities and figure_entity_label is not None:
            attributed = [
                fig for fig in group
                if figure_entity_label(str(fig.get("context", "")), entities)
            ]
            # Need at least two figures covering at least two DISTINCT
            # entities -- otherwise this is one side (or no side) talking.
            covered = {
                str(figure_entity_label(str(fig.get("context", "")), entities) or "").lower()
                for fig in attributed
            }
            covered.discard("")
            if len(attributed) < 2 or len(covered) < 2:
                logger.info(
                    "Comparison blocked: figures not attributable to distinct "
                    f"queried entities {entities}."
                )
                return None
            group = attributed[:6]
    except Exception as exc:
        # Fail-closed: attribution check unavailable -> block comparison.
        logger.warning("Figure-entity attribution check failed, blocking: %s", exc)
        return None
    return VisualOutput(
        visual_type="comparison",
        title="Comparison",
        props={
            "value": group[0]["value"],
            "baseline": group[1]["value"],
            "groups": [
                {
                    "label": f"{_figure_label(figure, queried_entities=entities)} [{figure['ref']}]",
                    "value": figure["value"],
                }
                for figure in group
            ],
        },
    )


def _outlook_status_visual(
    news_context: list, query: str
) -> Optional[VisualOutput]:
    """Citation-backed outlook badge for sentiment/opinion questions with no
    hard numbers: word-count sentiment over the snippets maps to
    on_track/at_risk/off_track. Detail always cites snippet numbers."""
    if not (news_context or []):
        return None
    if not SENTIMENT_INTENT_RE.search(query or ""):
        return None
    blob = " ".join(str(snippet).lower() for snippet in news_context)
    words = re.findall(r"[a-z]+", blob)
    if not words:
        return None
    positive = sum(1 for word in words if word in _POSITIVE_WORDS)
    negative = sum(1 for word in words if word in _NEGATIVE_WORDS)
    if positive > negative:
        state = "on_track"
        summary = "Sources lean positive"
    elif negative > positive:
        state = "off_track"
        summary = "Sources lean negative"
    else:
        state = "at_risk"
        summary = "Sources are mixed"
    top_ref = 1
    return VisualOutput(
        visual_type="status",
        title="Outlook",
        props={
            "state": state,
            "detail": f"{summary} across {len(news_context)} cited snippet(s) [1-{len(news_context)}]; top signal [{top_ref}].",
        },
    )


def _fundamentals_comparison_visual(
    fundamentals: list,
    query: Optional[str] = None,
) -> Optional[VisualOutput]:
    """Structured comparison from Yahoo fundamentals (no regex): market caps
    side by side when 2+ entities resolved.

    A CURRENT snapshot can never satisfy a historical growth/profitability
    request: when the query asks for "last N years", this returns None so a
    snapshot market-cap card cannot masquerade as a 3-year comparison.
    """
    try:
        if query and decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query) or {}
            if decomposed.get("requires_history"):
                logger.info(
                    "Snapshot fundamentals comparison blocked for historical query."
                )
                return None
    except Exception as exc:
        # Fail-closed: historical gate unavailable -> block snapshot comparison.
        logger.warning("Historical gate check failed, blocking comparison: %s", exc)
        return None
    caps = [
        item
        for item in (fundamentals or [])
        if isinstance(item, dict) and isinstance(item.get("market_cap"), (int, float))
    ]
    if len(caps) < 2:
        return None
    # Phase 11: market caps in KNOWN different currencies must not compare
    # as absolutes (CNY 100B next to USD 100B). Unknown currency on any side
    # passes with the code shown in the label when known.
    try:
        known = {
            str(item.get("currency", "") or "").strip().upper()
            for item in caps
        }
        known.discard("")
        if len(known) > 1:
            logger.info(
                "Snapshot fundamentals comparison blocked: mixed currencies %s.",
                sorted(known),
            )
            return None
    except Exception as exc:
        # Fail-closed: currency check unavailable -> block absolutes comparison.
        logger.warning("Currency check failed, blocking comparison: %s", exc)
        return None
    caps = caps[:6]
    return VisualOutput(
        visual_type="comparison",
        title="Market cap comparison",
        props={
            "value": caps[0]["market_cap"],
            "baseline": caps[1]["market_cap"],
            "groups": [
                {
                    "label": str(item.get("entity", item.get("symbol", "entity"))),
                    "value": item["market_cap"],
                }
                for item in caps
            ],
        },
    )


def _looks_like_date(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_roles(rows: Sequence[dict]) -> Dict[str, Any]:
    """Detect plottable column roles from a row sample (generic, topic-free):
    a date-like column, a low-cardinality text column, and a numeric column."""
    sample = [row for row in list(rows)[:_SYNTH_TABLE_MAX_ROWS] if isinstance(row, dict)]
    if not sample:
        return {}
    columns = list(sample[0].keys())
    roles: Dict[str, Any] = {"columns": columns}
    for column in columns:
        values = [row.get(column) for row in sample]
        present = [value for value in values if value is not None]
        if not present:
            continue
        numeric_count = sum(1 for value in present if _is_number(value))
        # A column stays numeric when numbers dominate: isolated
        # missing/non-numeric cells ("n/a") are skipped downstream, never
        # 0-filled, and must not demote the whole column to text.
        if numeric_count >= 2 and numeric_count >= len(present) / 2:
            roles.setdefault("numeric", []).append(column)
        elif sum(1 for value in present if _looks_like_date(value)) >= max(
            2, int(0.6 * len(present))
        ):
            # Date-like wins over the low-cardinality-text rule below, so a
            # date axis is never mistreated as bar categories.
            roles.setdefault("date", column)
        elif all(isinstance(value, str) for value in present) and len(set(present)) <= 12:
            roles.setdefault("category", column)
    return roles


def _visuals_from_rows(
    rows: Sequence[dict],
    computed_numbers: Optional[dict],
    preferred_visual: Optional[str] = None,
) -> list:
    """Deterministically synthesize visuals from real tool outputs (rows +
    code-computed stats). Every value comes from the supplied data - nothing
    is invented, so this is always honest. An explicitly requested shape
    (bar/line/pie/area/table/metric) is honored whenever the data supports it;
    otherwise the derivable default leads."""
    computed_numbers = computed_numbers or {}
    sample = list(rows)[:_SYNTH_TABLE_MAX_ROWS]
    columns = list(sample[0].keys())[:_SYNTH_TABLE_MAX_COLS]
    roles = _column_roles(rows)
    numeric = (roles.get("numeric") or [None])[0]
    parts: Dict[str, Any] = {"graph": None, "table": None, "metric": None}
    graph_basis: Optional[str] = None  # "date" | "category" | None

    if roles.get("date") and numeric and len(sample) >= 3:
        date_col = roles["date"]
        # Missing/non-numeric points are OMITTED (label and value together),
        # never coerced to literal 0: zero and missing are semantically
        # different, and a 0 invents a data point the evidence never had.
        labels: list = []
        values: list = []
        for row in sample:
            cell = row.get(numeric)
            if not _is_number(cell):
                continue
            labels.append(str(row.get(date_col)))
            values.append(cell)
        if len(labels) >= 3:
            parts["graph"] = {
                "visual_type": "graph",
                "title": f"{numeric} over time",
                "props": {
                    "chart_type": "line",
                    "labels": labels,
                    "datasets": [{"name": numeric, "values": values}],
                },
            }
            graph_basis = "date"
    elif roles.get("category") and numeric:
        category_col = roles["category"]
        # Metric-aware aggregation (P0#17): non-additive metrics (price,
        # margin, average, median, rate, percent, ratio) must never be
        # summed. SUM only for additive totals; otherwise AVG (mean of the
        # bucket), recorded explicitly in the title.
        _NON_ADDITIVE_RE = re.compile(
            r"(price|margin|average|avg|median|rate|percent|pct|ratio|"
            r"pe_ratio|p\/e|score|index)",
            re.IGNORECASE,
        )
        _agg = "AVG" if _NON_ADDITIVE_RE.search(str(numeric)) else "SUM"
        _bucket_vals: Dict[str, list] = {}
        for row in sample:
            key = str(row.get(category_col))
            value = row.get(numeric)
            # Non-numeric cells contribute nothing (never +0): a missing
            # value must not fabricate a zero-height bar or inflate totals.
            if not _is_number(value):
                continue
            _bucket_vals.setdefault(key, []).append(float(value))
        buckets: Dict[str, float] = {}
        for key, vals in _bucket_vals.items():
            if not vals:
                continue
            buckets[key] = (
                round(sum(vals) / len(vals), 2) if _agg == "AVG" else vals[0] + sum(vals[1:])
            )
        if 2 <= len(buckets) <= 12:
            _title = f"{numeric} by {category_col}"
            if _agg == "AVG":
                _title += " [avg]"
            parts["graph"] = {
                "visual_type": "graph",
                "title": _title,
                "props": {
                    "chart_type": "bar",
                    "labels": list(buckets.keys()),
                    "datasets": [{"name": f"{numeric} ({_agg})", "values": list(buckets.values())}],
                },
            }
            graph_basis = "category"

    # Honor an explicitly requested chart shape when the derived series allows
    # it (bar/line fit either series; pie needs categories, area needs dates).
    if parts["graph"] is not None and preferred_visual in ("bar", "line", "pie", "area"):
        allowed = {"bar", "line"}
        allowed |= {"pie"} if graph_basis == "category" else set()
        allowed |= {"area"} if graph_basis == "date" else set()
        if preferred_visual in allowed:
            parts["graph"]["props"]["chart_type"] = preferred_visual
            logger.info(f"Visual guarantee honored requested shape: {preferred_visual}.")
        else:
            logger.info(
                f"Requested shape {preferred_visual} not derivable; kept default."
            )

    table = {
        "visual_type": "table",
        "title": f"Results ({len(list(rows))} rows)",
        "props": {
            "columns": columns,
            "values": [[str(row.get(col, "")) for col in columns] for row in sample],
        },
    }
    parts["table"] = table

    totals = computed_numbers.get("totals") or {}
    averages = computed_numbers.get("averages") or {}
    if numeric and (numeric in totals or numeric in averages):
        value = totals.get(numeric, averages.get(numeric))
        metric_props: Dict[str, Any] = {
            "label": f"Total {numeric}" if numeric in totals else f"Average {numeric}",
            "value": value,
            "change_pct": None,
            "direction": "flat",
        }
        growth = computed_numbers.get("growth_pct") or {}
        if growth.get("metric") == numeric and growth.get("last_growth_pct") is not None:
            change = growth["last_growth_pct"]
            metric_props["change_pct"] = change
            metric_props["direction"] = (
                "up" if change > 0 else "down" if change < 0 else "flat"
            )
        parts["metric"] = {
            "visual_type": "metric",
            "props": metric_props,
            "title": f"{numeric}",
        }

    # Requested shape leads when present; otherwise graph, table, metric.
    if preferred_visual == "table":
        order = ["table", "graph", "metric"]
    elif preferred_visual == "metric":
        order = ["metric", "graph", "table"]
    else:
        order = ["graph", "table", "metric"]
    ordered = [parts[kind] for kind in order if parts[kind] is not None]
    # Validated VisualOutput objects (the field holds models, not dicts).
    return [VisualOutput(**visual) for visual in ordered[:3]]


def _align_series(
    series: list, max_points: int = _SYNTH_SERIES_MAX_POINTS
) -> tuple[list, list]:
    """Align multi-series timestamps before rendering (Phase 15 contract).

    Never assumes the first series' labels apply to every series: builds the
    sorted union of all timestamps, maps each series onto it (explicit None
    for missing observations -- never a neighbor's value, never 0), then
    downsamples the ALIGNED axis so labels and every dataset stay in lockstep.
    Returns (labels, datasets). Timestamps normalize to ISO day strings when
    parseable so "2024-01-01" and "Jan 01" style mixes still align; otherwise
    raw label text is the key.

    Semantic-channel guard (P0#15/#16): series with different stated
    metric/definition/frequency/unit/channel are NOT aligned together --
    the caller must filter first. As defense in depth, an explicit
    channel marker mismatch (macro vs market) raises instead of unioning.
    """
    from datetime import datetime as _datetime

    def _key(label: Any) -> str:
        text = str(label)
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d", "%b %d, %Y", "%Y/%m/%d"):
            try:
                parsed = _datetime.strptime(
                    text[: len(fmt)] if fmt.startswith("%b") else text[:10], fmt
                )
                if fmt == "%b %d":
                    return f"--{parsed.month:02d}-{parsed.day:02d}"
                return parsed.date().isoformat()
            except (ValueError, OverflowError):
                continue
        try:
            return _datetime.fromisoformat(text[:10]).date().isoformat()
        except (ValueError, TypeError):
            return text

    # Defense in depth: refuse to union series from different semantic
    # channels (e.g. FRED macro series mixed into market_data).
    try:
        channels = {
            str((item or {}).get("channel", "") or "").strip().lower()
            for item in (series or [])
            if isinstance(item, dict) and str((item or {}).get("channel", "") or "").strip()
        }
        if len(channels) > 1:
            raise ValueError(f"refusing to align semantically different channels {sorted(channels)}")
        metrics = {
            str((item or {}).get("metric", "") or "").strip().lower()
            for item in (series or [])
            if isinstance(item, dict) and str((item or {}).get("metric", "") or "").strip()
        }
        # "close" vs "close" aligns; "close" vs "cpi" never does.
        _MACRO_METRICS = {"cpi", "inflation", "unemployment", "gdp", "fed_funds", "treasury"}
        if metrics & _MACRO_METRICS and len(metrics) > 1:
            raise ValueError(f"refusing to align macro metric with market metric {sorted(metrics)}")
    except ValueError:
        raise
    except Exception:
        pass
    union: list[str] = []
    seen: set[str] = set()
    display: dict[str, str] = {}
    per_series: list[dict] = []
    for item in series:
        labels_in = list(item.get("labels") or [])
        values = list(item.get("values") or [])
        mapping = {_key(label): value for label, value in zip(labels_in, values)}
        per_series.append(mapping)
        for label in labels_in:
            key = _key(label)
            if key not in seen:
                seen.add(key)
                union.append(key)
                # First-seen original label wins for display, so ISO series
                # keep ISO labels and month-day series keep month-day labels.
                display[key] = str(label)
    union.sort()
    stride = max(1, len(union) // max_points)
    keys = union[::stride]
    labels = [display[key] for key in keys]
    datasets = []
    for item, mapping in zip(series, per_series):
        datasets.append(
            {
                "name": str(item.get("entity", item.get("symbol", "series"))),
                # Explicit None where this series has no observation: the
                # frontend renders a gap, never a fabricated 0.
                "values": [mapping.get(key) for key in keys],
            }
        )
    return labels, datasets


def _market_graph_visual(market_data: list, query: str) -> Optional[VisualOutput]:
    """Generalized market-series graph (replaces the old one-off hardcoded
    stock chart in the route): any entities, downsampled, neutral title.

    Gated: a one-month snapshot series NEVER charts for a multi-year
    request, and a single-entity series NEVER charts for a named
    two-company comparison. Both gates return None so the caller falls
    back to honest sources (never a mismatched chart).
    """
    entities = [item for item in market_data if item.get("values")]
    if not entities:
        return None
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            required = decomposed.get("entities", []) or []
            years = decomposed.get("period_years")
            is_comp = decomposed.get("is_comparison", False)
            # Partial-result policy: a validated subset with >=2 entities
            # may chart (excluded entities stated in prose); only an
            # insufficient subset (<2) blocks. Never zero-fill the missing.
            if is_comp and len(required) >= 2:
                have = {
                    str(item.get("entity", "")).strip().lower()
                    for item in entities
                }
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Market graph partial: missing %s for %s; "
                            "charting validated subset.",
                            missing, required,
                        )
                        # Filter to validated subset only (no placeholders).
                        _have = have
                        entities = [
                            item for item in entities
                            if str(item.get("entity", "")).strip().lower() in _have
                        ]
                    else:
                        logger.info(
                            "Market graph blocked: missing %s for comparison %s.",
                            missing, required,
                        )
                        return None
            # Short-term data cannot satisfy a multi-year request. EVERY
            # series is checked (H9/H14): the first series' span never
            # stands in for the rest.
            if years and validate_historical_coverage is not None:
                for item in entities:
                    ok, detail = validate_historical_coverage(
                        labels=list(item.get("labels") or []),
                        period_start=item.get("period_start"),
                        period_end=item.get("period_end"),
                        requested_years=years,
                        values=item.get("values"),
                    )
                    if not ok:
                        logger.info(
                            "Market graph blocked for historical query: %s: %s",
                            item.get("entity"), detail,
                        )
                        return None
    except Exception as exc:
        # Fail-closed: gate unavailable -> block the graph.
        logger.warning("Market graph gate check failed, blocking graph: %s", exc)
        return None
    entities = entities[:4]
    names = [str(item.get("entity", "series")) for item in entities]
    # Aligned timestamps: every dataset shares the union axis (Phase 15).
    labels, datasets = _align_series(entities)
    return VisualOutput(
        visual_type="graph",
        title=f"{', '.join(names)} performance",
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def _price_history_graph_visual(
    price_history: list, query: str, requested_years: Optional[int] = None
) -> Optional[VisualOutput]:
    """Validated multi-year price chart over the validated subset.

    Partial-result policy: charts the validated subset (>=2 entities with
    dated multi-year coverage) and never zero-fills the missing entity
    (exclusion stated in prose). Only an insufficient subset (<2) blocks.
    This is the ONLY chart allowed for "last N years" stock performance.
    Never invents.
    """
    series = [item for item in (price_history or []) if item.get("values") and item.get("labels")]
    if len(series) < 2:
        return None
    years = requested_years
    required: list = []
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            if years is None:
                years = decomposed.get("period_years")
            required = list(decomposed.get("entities", []) or [])
            # Partial: filter to validated-coverage subset; block only when
            # fewer than 2 survive. Missing entities are excluded, not filled.
            if decomposed.get("is_comparison") and len(required) >= 2:
                have = {
                    str(item.get("entity", "")).strip().lower() for item in series
                }
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Price-history graph partial: missing %s for %s; "
                            "charting validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Price-history graph blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
    except Exception as exc:
        # Fail-closed: entity check unavailable -> block the chart.
        logger.warning("Price-history entity check failed, blocking: %s", exc)
        return None
    if years and validate_historical_coverage is not None:
        for item in series:
            ok, _ = validate_historical_coverage(
                labels=list(item.get("labels") or []),
                period_start=item.get("period_start"),
                period_end=item.get("period_end"),
                requested_years=years,
                values=item.get("values"),
            )
            if not ok:
                logger.info(
                    "Price-history graph blocked: %s lacks %sY coverage.",
                    item.get("entity"), years,
                )
                return None
    # Like-for-like: same frequency when stated.
    freqs = {str(item.get("frequency", "") or "").lower() for item in series}
    freqs.discard("")
    if len(freqs) > 1:
        logger.info("Price-history graph blocked: mixed frequencies %s.", freqs)
        return None
    series = series[:4]
    names = [str(item.get("entity", item.get("symbol", "series"))) for item in series]
    # Aligned timestamps: no series inherits the first series' labels.
    # Missing observations render as gaps (None), never as a neighbor's
    # value or 0 (Phase 15).
    labels, datasets = _align_series(series)
    unit = str(series[0].get("currency", "") or "").strip()
    title = f"{', '.join(names)} stock performance"
    if years:
        title += f" ({years}Y)"
    try:
        series_currencies = {
            str(item.get("currency", "") or "").strip().upper() for item in series
        }
        series_currencies.discard("")
        if len(series_currencies) > 1:
            title += f" [mixed currencies {sorted(series_currencies)}: compare trends, not levels]"
        elif unit:
            title += f" [{unit}]"
    except Exception:
        if unit:
            title += f" [{unit}]"
    return VisualOutput(
        visual_type="graph",
        title=title,
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def _financial_history_table_visual(
    financial_history: list, metric: str = "revenue", query: Optional[str] = None,
) -> Optional[VisualOutput]:
    """Annual revenue / net-income table over the validated subset.

    Partial-result policy: tables the validated subset (>=2 companies with
    start+end rows); missing entities are excluded with prose notice, never
    zero-filled. Values are raw retrieved figures; growth/margin math lives
    in comparison_stats. Currency shown per row (absolutes in different
    currencies never like-for-like -- compare deterministic growth %).
    """
    rows: list[list[str]] = []
    by_entity: Dict[str, int] = {}
    currencies: set[str] = set()
    for item in financial_history or []:
        block = (item or {}).get(metric, {}) if isinstance(item, dict) else None
        if not isinstance(block, dict) or not block.get("values"):
            continue
        entity = str(item.get("entity", item.get("symbol", "entity")))
        currency = str(
            block.get("currency", "") or (item or {}).get("currency", "") or ""
        ).strip().upper()
        labels = list(block.get("labels") or [])
        values = list(block.get("values") or [])
        count = 0
        for label, value in zip(labels, values):
            try:
                rows.append(
                    [entity, str(label)[:10], f"{float(value):,.0f}", currency or "?"]
                )
                count += 1
            except (TypeError, ValueError):
                continue
        if currency:
            currencies.add(currency)
        by_entity[entity] = by_entity.get(entity, 0) + count
    # Every represented company needs start AND end; at least two overall.
    # Partial: drop single-point entities, keep the validated subset.
    by_entity = {k: v for k, v in by_entity.items() if v >= 2}
    rows = [r for r in rows if r[0] in by_entity]
    currencies = {r[3] for r in rows if r[3] and r[3] != "?"}
    if len(by_entity) < 2:
        return None
    # Named-comparison partial: validated subset charts; missing named
    # entities are excluded (prose notice), never zero-filled.
    if query and decompose_comparison_query is not None:
        try:
            required = (decompose_comparison_query(query) or {}).get("entities", []) or []
            if len(required) >= 2:
                have = {str(k).strip().lower() for k in by_entity}
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Financial table partial: missing %s for %s; "
                            "tabling validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Financial table blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
        except Exception as exc:
            # Fail-closed: entity check unavailable -> block the table.
            logger.warning("Financial table entity check failed, blocking: %s", exc)
            return None
    # Interleaved (never sorted-then-cut): a row cap must cost years, never
    # a whole company.
    rows = _interleave_entities(sorted(rows), _SYNTH_FINANCIAL_MAX_ROWS)
    base = "Annual revenue" if metric == "revenue" else "Annual net income"
    if len(currencies) == 1:
        title = f"{base} [{next(iter(currencies))}]"
    elif len(currencies) > 1:
        # Mixed currencies: absolutes are NOT comparable -- growth % is.
        title = (
            f"{base} [mixed currencies {sorted(currencies)}: compare "
            "growth %, not absolutes]"
        )
    else:
        title = f"{base} [currency as reported]"
    return VisualOutput(
        visual_type="table",
        title=title,
        props={"columns": ["Company", "Fiscal year", "Value", "Currency"], "values": rows},
    )


def _margin_table_visual(financial_history: list, query: Optional[str] = None) -> Optional[VisualOutput]:
    """Net-profit-margin table (net income / revenue * 100, computed here).

    One comparable profitability metric for every validated company.
    Partial: tables the validated subset (>=2 with computable margins);
    missing entities excluded with prose notice, never zero-filled.
    """
    rows: list[list[str]] = []
    for item in financial_history or []:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity", item.get("symbol", "entity")))
        rev = (item.get("revenue", {}) or {})
        inc = (item.get("net_income", {}) or {})
        rev_labels = list(rev.get("labels", []) or [])
        rev_values = list(rev.get("values", []) or [])
        inc_labels = list(inc.get("labels", []) or [])
        inc_values = list(inc.get("values", []) or [])
        if compute_net_margins is None:
            return None
        try:
            margins = compute_net_margins(
                rev_values, inc_values,
                revenue_labels=rev_labels or None,
                income_labels=inc_labels or None,
            )
        except Exception:
            continue
        for label, margin in zip(rev_labels, margins):
            if isinstance(margin, (int, float)):
                rows.append([entity, str(label)[:10], f"{margin:.2f}%"])
    entities = {row[0] for row in rows}
    if len(entities) < 2:
        return None
    # Named-comparison partial: validated subset tables; missing excluded.
    if query and decompose_comparison_query is not None:
        try:
            required = (decompose_comparison_query(query) or {}).get("entities", []) or []
            if len(required) >= 2:
                have = {str(name).strip().lower() for name in entities}
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Margin table partial: missing %s for %s; "
                            "tabling validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Margin table blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
        except Exception as exc:
            # Fail-closed: entity check unavailable -> block the table.
            logger.warning("Margin table entity check failed, blocking: %s", exc)
            return None
    rows = _interleave_entities(sorted(rows), _SYNTH_FINANCIAL_MAX_ROWS)
    return VisualOutput(
        visual_type="table",
        title="Net profit margin (net income / revenue)",
        props={"columns": ["Company", "Fiscal year", "Net margin"], "values": rows},
    )


def _visual_intent(query: str) -> str:
    """Requested semantic intent: what_if | historical_comparison | comparison | rows | qualitative."""
    q = query or ""
    if is_what_if_query(q):
        return "what_if"
    try:
        if decompose_comparison_query is not None:
            d = decompose_comparison_query(q) or {}
            if d.get("is_comparison") and d.get("requires_history"):
                return "historical_comparison"
            if d.get("is_comparison"):
                return "comparison"
    except Exception:
        pass
    if re.search(CHART_INTENT_RE, q, re.IGNORECASE):
        return "chart"
    return "qualitative"

__all__ = [
    "_align_series",
    "_column_roles",
    "_comparison_from_figures",
    "_comparison_intent_pool",
    "_drop_sources_table_visuals",
    "_figure_chart_group_key",
    "_figure_chart_groups",
    "_figures_bar_visual",
    "_figures_table_visual",
    "_financial_history_table_visual",
    "_fundamentals_comparison_visual",
    "_is_number",
    "_looks_like_date",
    "_margin_table_visual",
    "_market_graph_visual",
    "_outlook_status_visual",
    "_pick_chart_group",
    "_price_history_graph_visual",
    "_product_table_visual",
    "_recommended_from_snippets",
    "_recommended_table_visual",
    "_timeline_visual",
    "_visual_intent",
    "_visuals_from_rows",
]
