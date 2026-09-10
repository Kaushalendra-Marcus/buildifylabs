"""Guarded comparison-layer imports (verbatim move)."""


try:
    from app.services.data.comparison import (
        ComparisonEvidence,
        METRIC_PROFIT,
        METRIC_REVENUE,
        METRIC_STOCK,
        build_research_plan,
        build_trace,
        check_entity_completeness,
        check_research_completeness,
        clarification_asks_for_researchable_data,
        comparison_confidence,
        compute_comparison_stats,
        compute_net_margins,
        compute_pct_change,
        compute_yearly_stats,
        decompose_comparison_query,
        evidence_currency,
        evidence_driven_confidence,
        figures_share_metric,
        figure_entity_label,
        figure_metric_label,
        format_runtime_trace,
        insufficient_reason,
        is_comparison_query,
        is_figure_comparison_eligible,
        must_not_clarify,
        normalize_currency,
        parse_time_range,
        reconcile_judge_tools,
        required_tools_for_query,
        resolve_tool_plan,
        validate_calculation_inputs,
        validate_comparison,
        validate_historical_coverage,
    )
except Exception:  # pragma: no cover - import-time safety, never blocks pipeline
    ComparisonEvidence = None  # type: ignore
    METRIC_STOCK = "stock_performance"
    METRIC_REVENUE = "revenue_growth"
    METRIC_PROFIT = "profitability"
    build_research_plan = None  # type: ignore
    clarification_asks_for_researchable_data = None  # type: ignore
    comparison_confidence = None  # type: ignore
    compute_comparison_stats = None  # type: ignore
    compute_net_margins = None  # type: ignore
    compute_pct_change = None  # type: ignore
    compute_yearly_stats = None  # type: ignore
    decompose_comparison_query = None  # type: ignore
    evidence_currency = None  # type: ignore
    evidence_driven_confidence = None  # type: ignore
    figures_share_metric = None  # type: ignore
    figure_entity_label = None  # type: ignore
    figure_metric_label = None  # type: ignore
    insufficient_reason = None  # type: ignore
    is_comparison_query = None  # type: ignore
    must_not_clarify = None  # type: ignore
    normalize_currency = None  # type: ignore
    parse_time_range = None  # type: ignore
    build_trace = None  # type: ignore
    check_entity_completeness = None  # type: ignore
    check_research_completeness = None  # type: ignore
    format_runtime_trace = None  # type: ignore
    is_figure_comparison_eligible = None  # type: ignore
    reconcile_judge_tools = None  # type: ignore
    required_tools_for_query = None  # type: ignore
    resolve_tool_plan = None  # type: ignore
    validate_calculation_inputs = None  # type: ignore
    validate_comparison = None  # type: ignore
    validate_historical_coverage = None  # type: ignore
