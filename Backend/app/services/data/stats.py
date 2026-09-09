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

    changes = []
    for i in range(1, len(values)):
        prior, current = values[i - 1], values[i]
        # Explicit zero-base handling: 0 prior -> undefined (skipped), never
        # falsy-dropped valid zeros as current values. None/NaN checked
        # explicitly, never via truthiness (0.0 is a valid observation).
        try:
            import math as _math

            if prior is None or current is None:
                continue
            prior_f, cur_f = float(prior), float(current)
            if not (_math.isfinite(prior_f) and _math.isfinite(cur_f)):
                continue
            if prior_f == 0:
                continue
            changes.append(round((cur_f - prior_f) / abs(prior_f) * 100, 2))
        except (TypeError, ValueError):
            continue
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
    """Total-to-total ratio of the first two numeric columns (margin-style).

    Explicit denominator-zero check (0 is meaningful, not just falsy).
    Ratios assume both columns share the same row grain; callers must not
    present this as a margin unless the columns are semantically
    numerator/denominator compatible (documented assumption, not verified).
    """
    if len(numeric) < 2:
        return None
    numerator, denominator = numeric[0], numeric[1]
    try:
        import math as _math

        numerator_total = float(df[numerator].dropna().sum())
        denominator_total = float(df[denominator].dropna().sum())
        if not (_math.isfinite(numerator_total) and _math.isfinite(denominator_total)):
            return None
    except (TypeError, ValueError):
        return None
    if denominator_total == 0:
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
    # Missing != zero (P0#10 audit): rows with missing price/quantity are
    # excluded from the baseline (never fillna(0)), and the excluded count
    # is reported so narration can state it. Only an explicit semantic
    # "missing means zero" operation may zero-fill (none defined here).
    excluded_rows = 0
    if qty_col is not None and _is_numeric(df[qty_col]):
        paired = df[[price_col, qty_col]].dropna()
        excluded_rows = int(len(df) - len(paired))
        baseline_total = float((paired[price_col] * paired[qty_col]).sum())
        scenario_total = baseline_total * factor
        basis = f"{price_col} x {qty_col}"
    elif revenue_col is not None and _is_numeric(df[revenue_col]):
        present = df[revenue_col].dropna()
        excluded_rows = int(len(df) - len(present))
        baseline_total = float(present.sum())
        scenario_total = baseline_total * factor
        basis = f"{revenue_col} scaled by the price factor"
    else:
        present = df[price_col].dropna()
        excluded_rows = int(len(df) - len(present))
        baseline_total = float(present.sum())
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
        "excluded_rows_missing": excluded_rows,
        "computation_id": "what_if",
        "formula": "scenario_total = baseline_total * (1 + pct/100)",
        "operation": "price_scenario_scale",
        "inputs": {"pct_change": pct, "factor": round(factor, 4), "basis": basis},
        "assumption": (
            "Assumes quantity is unaffected by the price change "
            "(no elasticity modeled)."
        ),
    }


# ---------------------------------------------------------------------------
# Free-form scenario what-if: the question states its OWN baseline numbers
# in the text ("5,000 customers paying $100/month") with no uploaded data
# required at all, then asks for a scenario recompute ("if price +25%,
# 18% churn, costs -12%, what's the new revenue"). Distinct from
# parse_what_if/apply_what_if above, which scale an existing DATA TABLE's
# price column -- this has no rows to scale, the numbers ARE the query.
# Same deterministic discipline: a baseline or lever that isn't explicitly,
# unambiguously stated is never guessed -- return None (or omit that piece)
# rather than fabricate a number the LLM would otherwise compute silently.
# ---------------------------------------------------------------------------

_SCENARIO_COUNT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(k|K|thousand|m|M|million)?\s*"
    r"(customers?|users?|subscribers?|clients?|members?)",
    re.IGNORECASE,
)
_SCENARIO_PRICE_RE = re.compile(
    r"(?:paying|charging|at|for)?\s?"
    r"([$\u20ac\u20b9\u00a3])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|thousand|m|M|million)?"
    r"\s?(?:/|\bper\b\s?)(month|mo|year|yr)?",
    re.IGNORECASE,
)
_SCENARIO_SCALE = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
}
_SCENARIO_PRICE_CUE_RE = re.compile(
    r"\b(price|prices|pricing|fee|fees|rate|rates|subscription)\b",
    re.IGNORECASE,
)
_SCENARIO_CUSTOMER_CUE_RE = re.compile(
    r"\b(customers?|users?|subscribers?|clients?|members?|churn\w*)\b",
    re.IGNORECASE,
)
_SCENARIO_COST_CUE_RE = re.compile(
    r"\b(cost|costs|expense|expenses|spending|operating\s*cost\w*)\b",
    re.IGNORECASE,
)
_SCENARIO_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
# Down/up direction cues for a scenario lever's local window. Superset of
# WHAT_IF_DOWN_RE/WHAT_IF_UP_RE above: "lose"/"churn" are how customer-count
# decreases are actually phrased ("lose 18% of customers"), not covered by
# the price-scenario direction words alone.
_SCENARIO_DOWN_RE = re.compile(
    r"\b(lower\w*|drop\w*|decreas\w*|down|cuts?|cutting|reduc\w*|less|"
    r"fewer|lose|losing|lost|churn\w*|fall\w*|declin\w*)\b",
    re.IGNORECASE,
)
_SCENARIO_UP_RE = re.compile(
    r"\b(rais\w*|ris\w*|rose|increas\w*|up|higher|hik\w*|more|grow\w*|"
    r"gain\w*|add\w*)\b",
    re.IGNORECASE,
)
# Backstop trigger for is_what_if_query(): a conditional numeric scenario
# that never says "what if" ("If I raise prices 25%, ..., calculate the new
# revenue") but is unmistakably the same intent. Used even when
# compute_freeform_scenario can't cleanly extract a baseline, so the
# pipeline still avoids treating it as a historical/market-history query.
CONDITIONAL_SCENARIO_RE = re.compile(
    r"\bif\b.{0,120}?\d+(?:\.\d+)?\s?%.{0,120}?"
    r"\b(calculate|find|show|estimate|determine|project|"
    r"what\s+(?:will|would|is|are)|how\s+much)\b",
    re.IGNORECASE | re.DOTALL,
)


# Clause boundary for lever attribution: comma+space (deliberately NOT a
# bare comma -- "5,000" is a thousands separator with no space after the
# comma, so it must never be split) optionally consuming a following
# and/but, OR a standalone and/but, OR sentence-end. Each percent figure is
# judged only against its OWN clause's direction/target cues. A fixed
# character window was tried first and rejected: in a dense compound
# sentence ("increase price 25%, lose 18% of customers"), a neighboring
# clause's direction word can sit CLOSER to a percent than that percent's
# own direction word, so nearest-in-a-window is wrong -- nearest-in-clause
# is what actually disambiguates it (verified by execution, not just read).
_CLAUSE_SPLIT_RE = re.compile(r",\s+(?:and\s+|but\s+)?|\s+and\s+|\s+but\s+|[.;]\s*")


def _scenario_direction(clause: str) -> Optional[int]:
    """+1 for an increase/gain cue, -1 for a decrease/loss cue within a
    single clause, None when neither (or both, genuinely ambiguous) is
    present in that clause."""
    down = bool(_SCENARIO_DOWN_RE.search(clause or ""))
    up = bool(_SCENARIO_UP_RE.search(clause or ""))
    if down and not up:
        return -1
    if up and not down:
        return 1
    return None


def parse_scenario_baseline(query: str) -> dict:
    """Extract a stated baseline customer count and per-period price from
    the query text ("5,000 customers paying $100/month"). Best-effort:
    either or both fields are simply absent when not confidently found --
    never guessed from context."""
    out: dict = {}
    text = query or ""
    count_match = _SCENARIO_COUNT_RE.search(text)
    if count_match:
        try:
            raw = count_match.group(1).replace(",", "")
            scale = _SCENARIO_SCALE.get((count_match.group(2) or "").lower(), 1)
            out["count"] = float(raw) * scale
        except (TypeError, ValueError):
            pass
    price_match = _SCENARIO_PRICE_RE.search(text)
    if price_match:
        try:
            raw = price_match.group(2).replace(",", "")
            scale = _SCENARIO_SCALE.get((price_match.group(3) or "").lower(), 1)
            out["price"] = float(raw) * scale
            out["price_period"] = (price_match.group(4) or "month").lower()
        except (TypeError, ValueError):
            pass
    return out


def parse_scenario_levers(query: str) -> dict:
    """Extract {price_pct, customer_pct, cost_pct} levers stated in the
    query. The text is split into clauses first (see _CLAUSE_SPLIT_RE) so
    each percent figure is attributed using only ITS OWN clause's
    direction/target cues -- not a fixed character window, which was tried
    and rejected by execution: in "increase price 25%, lose 18% of
    customers" a neighboring clause's direction word can be nearer in raw
    characters than the correct one. Within a clause, the NEAREST target
    cue to the percent wins (nearest-cue-wins, same discipline used for
    financial-figure attribution elsewhere in this codebase). A percent
    with no direction cue, or no target cue, in its clause is dropped, not
    guessed. First clause wins per lever type if a lever type somehow
    appears twice."""
    text = query or ""
    levers: dict = {}
    cue_patterns = (
        ("price_pct", _SCENARIO_PRICE_CUE_RE),
        ("customer_pct", _SCENARIO_CUSTOMER_CUE_RE),
        ("cost_pct", _SCENARIO_COST_CUE_RE),
    )
    for clause in _CLAUSE_SPLIT_RE.split(text):
        pct_match = _SCENARIO_PCT_RE.search(clause)
        if not pct_match:
            continue
        try:
            pct = float(pct_match.group(1))
        except (TypeError, ValueError):
            continue
        direction = _scenario_direction(clause)
        if direction is None:
            continue
        signed = pct * direction
        pct_pos = pct_match.start()
        best_key: Optional[str] = None
        best_dist = 10 ** 9
        for key, pattern in cue_patterns:
            for cue_match in pattern.finditer(clause):
                dist = abs(cue_match.start() - pct_pos)
                if dist < best_dist:
                    best_dist = dist
                    best_key = key
        if best_key and best_key not in levers:
            levers[best_key] = signed
    return levers


def compute_freeform_scenario(query: str) -> Optional[dict]:
    """Deterministic recompute for a self-contained business scenario
    stated entirely in the query text (no uploaded data needed): "5,000
    customers paying $100/month. If I increase the price by 25%, lose 18%
    of customers, and reduce operating costs by 12%, calculate the new
    monthly revenue..." Returns None whenever no lever is found, or no
    customer-count AND price baseline are both stated -- never a guessed
    revenue figure. A cost lever with no stated baseline cost is reported
    as a relative change only (see cost_note), never an invented dollar
    amount. Output uses the same keys as apply_what_if/
    compute_structured_what_if (target/pct_change/baseline_total/
    scenario_total/delta/assumption) so it's a drop-in for
    computed_numbers["what_if"]."""
    levers = parse_scenario_levers(query or "")
    if not levers:
        return None
    baseline = parse_scenario_baseline(query or "")
    if "count" not in baseline or "price" not in baseline:
        return None
    count = baseline["count"]
    price = baseline["price"]
    baseline_revenue = count * price
    if baseline_revenue == 0:
        return None
    new_count = count * (1 + levers.get("customer_pct", 0.0) / 100.0)
    new_price = price * (1 + levers.get("price_pct", 0.0) / 100.0)
    new_revenue = new_count * new_price
    revenue_pct_change = round(
        (new_revenue - baseline_revenue) / abs(baseline_revenue) * 100, 2
    )
    period = baseline.get("price_period", "month")
    result: dict = {
        "target": "revenue",
        "pct_change": revenue_pct_change,
        "baseline_total": round(baseline_revenue, 2),
        "scenario_total": round(new_revenue, 2),
        "delta": round(new_revenue - baseline_revenue, 2),
        "baseline_count": round(count, 2),
        "baseline_price": round(price, 2),
        "scenario_count": round(new_count, 2),
        "scenario_price": round(new_price, 2),
        "price_period": period,
        "levers": {k: round(v, 2) for k, v in levers.items()},
        "basis": (
            f"baseline revenue = stated count x stated price/{period}; "
            "each stated lever (price, customer count) is applied to its "
            "own baseline figure independently, then revenue is "
            "recomputed from the new count and new price."
        ),
        "assumption": (
            "Assumes each stated change (price, customer count, cost) "
            "applies independently to the baseline figures given in the "
            "question; no interaction between levers (e.g. price "
            "elasticity driving the customer-count change) is modeled "
            "beyond what was explicitly stated."
        ),
        "computation_id": "freeform_scenario",
        "formula": (
            "scenario_total = (count * (1 + customer_pct/100)) * "
            "(price * (1 + price_pct/100))"
        ),
    }
    if "cost_pct" in levers:
        result["cost_pct_change"] = round(levers["cost_pct"], 2)
        result["cost_note"] = (
            "No baseline operating-cost figure was stated in the "
            "question, so only the relative change "
            f"({levers['cost_pct']:+.1f}%) can be reported here -- not a "
            "dollar impact."
        )
    return result

