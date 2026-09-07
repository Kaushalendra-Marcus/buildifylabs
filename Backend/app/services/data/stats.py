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
- `what_if` (specs/11 §3.3 v1) - parameterized price-scenario recompute via
  `apply_what_if`: "raise price 10%" scales the price-like column and
  recomputes the revenue total deterministically, stating the
  quantity-unaffected assumption for the LLM to narrate verbatim.
"""

import re
from typing import List, Optional

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


# specs/11 §3.3 v1 - what-if scenarios. Column mapping is by generic name
# match only (no business-concept guessing): a price-like column and a
# quantity-like column. Revenue is the revenue-like column when present,
# else price x quantity. Anything missing -> None (no scenario), never a
# fabricated number.
WHAT_IF_PRICE_NAMES = ("price", "unit_price", "unitprice", "rate", "mrp")
WHAT_IF_QTY_NAMES = ("quantity", "qty", "units", "unit", "count", "volume")
WHAT_IF_REVENUE_NAMES = ("revenue", "sales", "total", "amount", "turnover")

# "what if I raise price 10%", "what happens if price drops 5%",
# "increase discount by 20%" - target + percent. Direction words live
# anywhere in the what-if span (before or after the target), so they are
# detected in Python over the matched span instead of an optional regex
# group (lazy matching would always prefer the empty alternative).
WHAT_IF_RE = re.compile(
    r"\bwhat(?:\s+\w+){0,4}\s+if\b.{0,80}?"
    r"\b(price|discount|rate)s?\b.{0,40}?(\d+(?:\.\d+)?)\s?%",
    re.IGNORECASE,
)
WHAT_IF_DOWN_RE = re.compile(
    r"\b(lower\w*|drop\w*|decreas\w*|down|cuts?|cutting|reduc\w*|less|fewer)\b",
    re.IGNORECASE,
)
WHAT_IF_UP_RE = re.compile(
    r"\b(rais\w*|ris\w*|rose|increas\w*|up|higher|hik\w*|more|grow\w*)\b",
    re.IGNORECASE,
)


def parse_what_if(query: str) -> Optional[tuple[str, float]]:
    """Extract a (target, signed_pct) price scenario from the query.

    "Raise price 10%" -> ("price", 10.0); "drop price 5%" -> ("price", -5.0).
    A bare "what if price changes 10%" defaults to +10%. Discounts invert:
    "increase discount 20%" lowers the effective price (-20%). None when the
    query carries no price-scenario intent.
    """
    match = WHAT_IF_RE.search(query or "")
    if not match:
        return None
    target = match.group(1).lower()
    pct = float(match.group(2))
    span = match.group(0)
    down = bool(WHAT_IF_DOWN_RE.search(span))
    up = bool(WHAT_IF_UP_RE.search(span))
    if target == "discount":
        # A bigger discount means a lower effective price and vice versa.
        target = "price"
        pct = pct if down and not up else -pct
    elif down and not up:
        pct = -pct
    if pct == 0:
        return None
    return target, pct


def _find_column(columns: List[str], names: tuple[str, ...]) -> Optional[str]:
    lowered = {col.lower(): col for col in columns}
    for name in names:
        if name in lowered:
            return lowered[name]
    for col in columns:
        if any(name in col.lower() for name in names):
            return col
    return None


def apply_what_if(
    rows: List[dict], target: str, pct: float
) -> Optional[dict]:
    """Deterministically recompute revenue under a price scenario.

    Scales the price-like column by (1 + pct/100) and recomputes the total
    from price x quantity (or the revenue-like column scaled by the same
    factor when no quantity column exists). v1 assumes quantity is
    unaffected by the price change - the returned `assumption` string must
    be stated verbatim in the answer (specs/11 §3.3). None when the data
    cannot support the scenario (no price column).
    """
    if not rows or target != "price" or not pct:
        return None
    df = _to_frame(rows)
    columns = list(df.columns)
    price_col = _find_column(columns, WHAT_IF_PRICE_NAMES)
    if price_col is None or not _is_numeric(df[price_col]):
        return None
    qty_col = _find_column(columns, WHAT_IF_QTY_NAMES)
    revenue_col = _find_column(columns, WHAT_IF_REVENUE_NAMES)
    factor = 1.0 + pct / 100.0
    if factor <= 0:
        return None
    baseline_prices = df[price_col].dropna()
    if baseline_prices.empty:
        return None
    if qty_col is not None and _is_numeric(df[qty_col]):
        baseline_total = float((df[price_col].fillna(0) * df[qty_col].fillna(0)).sum())
        scenario_total = baseline_total * factor
        basis = f"{price_col} x {qty_col}"
    elif revenue_col is not None and _is_numeric(df[revenue_col]):
        baseline_total = float(df[revenue_col].fillna(0).sum())
        scenario_total = baseline_total * factor
        basis = f"{revenue_col} scaled by the price factor"
    else:
        baseline_total = float(df[price_col].fillna(0).sum())
        scenario_total = baseline_total * factor
        basis = f"{price_col} total scaled by the price factor"
    baseline_total = round(baseline_total, 2)
    scenario_total = round(scenario_total, 2)
    return {
        "target": "price",
        "pct_change": pct,
        "factor": round(factor, 4),
        "baseline_total": baseline_total,
        "scenario_total": scenario_total,
        "delta": round(scenario_total - baseline_total, 2),
        "basis": basis,
        "price_column": price_col,
        "quantity_column": qty_col,
        "assumption": (
            "Assumes quantity is unaffected by the price change "
            "(no elasticity modeled)."
        ),
    }
