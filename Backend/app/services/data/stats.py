"""Deterministic statistics computed in pandas (specs/11 §3.1, Phase B4).

Core design rule of specs/11 §2: **the LLM never does arithmetic.** This module
computes the numbers (averages, period-over-period growth %, ratios) from the
executed rows; `run_pipeline` then feeds them into the prompt as facts the LLM
only narrates.

Everything here is deliberately simple and deterministic - the point is honest,
reproducible numbers, not sophisticated modeling (forecasting/what-ifs are B6).
- `averages` / `totals` / `mins` / `maxs` per numeric column.
- `growth_pct` - period-over-period % change of the first numeric column,
  grouped by the first date-like column when one exists (>= 2 periods).
- `ratios` - total-to-total ratio of the first two numeric columns (e.g. a
  margin-style ratio) when both exist.
"""

from typing import List

import pandas as pd


def compute_statistics(rows: List[dict]) -> dict:
    """Compute deterministic summary statistics from the executed rows.

    Returns a dict of facts (safe to serialize into the prompt). Missing
    capabilities are simply absent from the result - never fabricated.
    """
    result: dict = {"row_count": len(rows)}
    if not rows:
        return result

    df = _to_frame(rows)
    numeric = [c for c in df.columns if _is_numeric(df[c]) and c.lower() != "id"]

    if numeric:
        result["averages"] = {c: _round(df[c].mean()) for c in numeric}
        result["totals"] = {c: _round(df[c].sum()) for c in numeric}
        result["mins"] = {c: _round(df[c].min()) for c in numeric}
        result["maxs"] = {c: _round(df[c].max()) for c in numeric}

        growth = _growth_period_over_period(df, numeric)
        if growth:
            result["growth_pct"] = growth

        ratios = _pairwise_totals_ratio(df, numeric)
        if ratios:
            result["ratios"] = ratios

    return result


def _to_frame(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    return _coerce_date_columns(df)


def _coerce_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce string date-like columns to datetime64 so date grouping works.

    Drivers return DateTime cells as Python `datetime` (Postgres) or ISO strings
    (SQLite); normalizing both to datetime64 lets the growth calc group by date
    regardless of which DB the rows came from.
    """
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        if not (
            pd.api.types.is_object_dtype(df[col])
            or pd.api.types.is_string_dtype(df[col])
        ):
            continue
        probe = pd.to_datetime(df[col], errors="coerce")
        present = int(df[col].notna().sum())
        if present and int(probe.notna().sum()) >= max(1, int(0.5 * present)):
            df[col] = probe
    return df


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series)


def _round(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return round(value, 2)


def _growth_period_over_period(df: pd.DataFrame, numeric: List[str]) -> dict | None:
    """Period-over-period % change for the first date column x first numeric.

    Returns None when there is no date column or fewer than 2 periods (an
    insufficient series is reported as absent, not forced - specs/11 §5).
    """
    date_cols = [
        c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])
    ]
    if not date_cols or not numeric:
        return None
    date_col, metric = date_cols[0], numeric[0]

    series = df.sort_values(date_col).groupby(df[date_col])[metric].sum()
    values = [float(v) for v in series.tolist()]
    if len(values) < 2:
        return None

    changes = [
        round((values[i] - values[i - 1]) / abs(values[i - 1]) * 100, 2)
        for i in range(1, len(values))
        if values[i - 1]
    ]
    if not changes:
        return None

    return {
        "metric": metric,
        "periods": len(values),
        "earliest_total": round(values[0], 2),
        "latest_total": round(values[-1], 2),
        "last_growth_pct": changes[-1],
        "avg_growth_pct": round(sum(changes) / len(changes), 2),
    }


def _pairwise_totals_ratio(df: pd.DataFrame, numeric: List[str]) -> dict | None:
    """Total-to-total ratio of the first two numeric columns (margin-style)."""
    if len(numeric) < 2:
        return None
    numerator, denominator = numeric[0], numeric[1]
    numerator_total = float(df[numerator].sum())
    denominator_total = float(df[denominator].sum())
    if not denominator_total:
        return None
    return {
        f"{numerator}_to_{denominator}": round(
            numerator_total / denominator_total, 4
        )
    }
