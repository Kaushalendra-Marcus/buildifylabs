"""Deterministic statistics (specs/11 §3.1) - the numbers /chat feeds the LLM.

Core rule (specs/11 §2): the LLM never does arithmetic; these functions ARE the
arithmetic. Tests prove the numbers are computed in pandas, deterministic, and
safely serializable (no NaN/None leaks that would show up in the prompt as
"nan").
"""

from app.services.data.stats import compute_statistics


def test_empty_rows_yield_only_row_count():
    assert compute_statistics([]) == {"row_count": 0}


def test_averages_totals_bounds_for_numeric_columns():
    stats = compute_statistics(
        [
            {"date": "2024-01-01", "revenue": 100, "region": "east"},
            {"date": "2024-01-02", "revenue": 250, "region": "west"},
        ]
    )
    assert stats["row_count"] == 2
    assert stats["averages"] == {"revenue": 175.0}
    assert stats["totals"] == {"revenue": 350.0}
    assert stats["mins"] == {"revenue": 100.0}
    assert stats["maxs"] == {"revenue": 250.0}


def test_id_column_is_excluded_from_numeric_stats():
    stats = compute_statistics(
        [{"id": 1, "revenue": 10}, {"id": 2, "revenue": 30}]
    )
    assert "id" not in stats["averages"]
    assert stats["averages"] == {"revenue": 20.0}


def test_growth_period_over_period():
    stats = compute_statistics(
        [
            {"date": "2024-01-01", "revenue": 100},
            {"date": "2024-01-02", "revenue": 250},
            {"date": "2024-01-03", "revenue": 300},
        ]
    )
    growth = stats["growth_pct"]
    assert growth["metric"] == "revenue"
    assert growth["periods"] == 3
    assert growth["earliest_total"] == 100.0
    assert growth["latest_total"] == 300.0
    # (250 -> 100) is +150%, (300 -> 250) is +20%; average of the two = 85%
    assert growth["last_growth_pct"] == 20.0
    assert growth["avg_growth_pct"] == 85.0


def test_single_period_has_no_growth():
    stats = compute_statistics([{"date": "2024-01-01", "revenue": 100}])
    assert "growth_pct" not in stats


def test_no_date_column_means_no_growth():
    stats = compute_statistics([{"revenue": 100}, {"revenue": 200}])
    assert "growth_pct" not in stats
    assert stats["averages"] == {"revenue": 150.0}


def test_ratio_of_first_two_numeric_columns():
    stats = compute_statistics(
        [
            {"revenue": 100, "cost": 50},
            {"revenue": 200, "cost": 100},
        ]
    )
    assert stats["ratios"] == {"revenue_to_cost": 2.0}


def test_no_ratio_when_denominator_is_zero():
    stats = compute_statistics([{"revenue": 100, "cost": 0}])
    assert "ratios" not in stats


def test_datetime_objects_from_postgres_style_driver():
    # Postgres returns datetime.datetime; growth must still detect the date col.
    from datetime import datetime

    stats = compute_statistics(
        [
            {"date": datetime(2024, 1, 1), "revenue": 100},
            {"date": datetime(2024, 1, 2), "revenue": 200},
        ]
    )
    assert stats["growth_pct"]["last_growth_pct"] == 100.0


def test_nan_values_do_not_leak_into_numbers():
    stats = compute_statistics(
        [{"date": "2024-01-01", "revenue": None}, {"date": "2024-01-02", "revenue": 200}]
    )
    # average of (NaN, 200) -> 200; no None/NaN in the dict values.
    assert stats["averages"]["revenue"] == 200.0
    for value in stats["averages"].values():
        assert value is not None