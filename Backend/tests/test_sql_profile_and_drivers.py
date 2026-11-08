"""LIMIT cap, SQL interpretation, data profile, driver decomposition, result checks."""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.middlewares.sql_sanitizer import MAX_QUERY_LIMIT, sanitize_sql
from app.services.data.drivers import compute_drivers, is_driver_query
from app.services.data.executor import get_data_profile
from app.services.data.result_checks import (
    ALL_EMPTY,
    EMPTY_BUT_DATA,
    HIT_LIMIT,
    compact_profile,
    should_cap_confidence,
    sql_result_sanity,
)
from app.services.llm.sql_generator import build_data_schema, describe_sql


# --- LIMIT cap -------------------------------------------------------------------

def test_limit_at_the_cap_is_allowed():
    assert sanitize_sql(f"SELECT a FROM t LIMIT {MAX_QUERY_LIMIT}")


def test_limit_above_the_cap_is_rejected():
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("SELECT a FROM t LIMIT 100000000")
    assert exc.value.status_code == 403


def test_missing_limit_is_still_rejected():
    with pytest.raises(HTTPException):
        sanitize_sql("SELECT a FROM t")


# --- SQL interpretation + schema prompt -------------------------------------------

def test_describe_sql_reads_like_plain_english():
    description = describe_sql(
        "SELECT region, SUM(revenue) AS total FROM user_abc123_data "
        "WHERE status = 'paid' GROUP BY region ORDER BY total DESC LIMIT 100"
    )
    assert description.startswith("Reads your data table: selects")
    assert "grouped by region" in description
    assert "filtered to status = 'paid'" in description
    assert "limited to 100 rows" in description


def test_describe_sql_never_raises_on_garbage():
    assert describe_sql("not sql at all ((") in ("", )


def test_schema_without_profile_is_unchanged():
    assert build_data_schema("t", ["a", "b"]) == "Tables:\nt\n- a\n- b"


def test_schema_with_profile_adds_real_facts_and_date_anchor():
    profile = {
        "row_count": 56,
        "date_column": "order_date",
        "date_max": "2025-03-28",
        "columns": [
            {"name": "order_date", "type": "date", "iso_text": True,
             "min": "2025-03-01", "max": "2025-03-28", "nulls": 0},
            {"name": "region", "type": "text", "distinct": 2,
             "top_values": ["East", "West"], "nulls": 0},
        ],
    }
    schema = build_data_schema("t", ["order_date", "region"], profile)
    assert "Latest date in the data: 2025-03-28" in schema
    assert "never CURRENT_DATE" in schema
    assert "'East', 'West'" in schema
    assert "ISO dates" in schema  # the column is text, and the model is told so


# --- profile + drivers against a real (SQLite) table ------------------------------

def _scenario():
    async def run():
        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.execute(text(
                'CREATE TABLE "user_x_data" '
                '("order_date" TEXT, "region" TEXT, "revenue" INTEGER)'
            ))
            rows = []
            for day in range(1, 29):
                stamp = f"2025-03-{day:02d}"
                rows.append({"d": stamp, "r": "East", "v": 100})
                rows.append({"d": stamp, "r": "West", "v": 100 if day < 16 else 40})
            await conn.execute(text('INSERT INTO "user_x_data" VALUES (:d, :r, :v)'), rows)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            profile = await get_data_profile(
                db, "user_x_data", ["order_date", "region", "revenue"]
            )
            drivers = await compute_drivers(db, "user_x_data", profile)
        await engine.dispose()
        return profile, drivers

    return asyncio.run(run())


def test_profile_describes_the_table():
    profile, _ = _scenario()
    assert profile["row_count"] == 56
    assert profile["date_column"] == "order_date"
    assert (profile["date_min"], profile["date_max"]) == ("2025-03-01", "2025-03-28")
    region = next(c for c in profile["columns"] if c["name"] == "region")
    assert region["distinct"] == 2
    assert sorted(region["top_values"]) == ["East", "West"]
    revenue = next(c for c in profile["columns"] if c["name"] == "revenue")
    assert (revenue["min"], revenue["max"]) == (40, 100)


def test_drivers_attribute_the_change_to_the_right_segment():
    _, drivers = _scenario()
    assert drivers is not None
    assert drivers["metric"] == "revenue"
    assert drivers["recent_period"] == "2025-03-16 to 2025-03-28"
    assert drivers["prior_total"] == 2600.0
    assert drivers["recent_total"] == 1820.0
    assert drivers["change"] == -780.0
    assert drivers["pct_change"] == -30.0
    top = drivers["top_contributors"][0]
    assert (top["dimension"], top["value"]) == ("region", "West")
    assert top["change"] == -780.0
    assert top["share_of_change_pct"] == 100.0


def test_drivers_need_a_date_axis():
    assert asyncio.run(compute_drivers(None, "t", {"columns": [], "date_column": None})) is None


def test_driver_query_detection():
    assert is_driver_query("Why did revenue drop last week?")
    assert is_driver_query("what changed in sales")
    assert not is_driver_query("What is the total revenue?")


# --- result checks -----------------------------------------------------------------

def test_empty_result_on_nonempty_table_is_noted():
    notes = sql_result_sanity([], "SELECT 1 LIMIT 1", {"row_count": 10})
    assert [n["code"] for n in notes] == [EMPTY_BUT_DATA]
    assert not should_cap_confidence(notes)


def test_all_empty_values_cap_confidence():
    notes = sql_result_sanity([{"total": None}], "SELECT SUM(x) AS total FROM t LIMIT 100")
    assert [n["code"] for n in notes] == [ALL_EMPTY]
    assert should_cap_confidence(notes)


def test_hitting_the_row_limit_is_noted():
    notes = sql_result_sanity([{"a": 1}, {"a": 2}], "SELECT a FROM t LIMIT 2")
    assert HIT_LIMIT in [n["code"] for n in notes]


def test_healthy_result_has_no_notes():
    assert sql_result_sanity([{"a": 1}], "SELECT a FROM t LIMIT 100") == []


def test_compact_profile_is_small_and_json_safe():
    compact = compact_profile({
        "row_count": 5, "date_column": "d", "date_min": "2025-01-01", "date_max": "2025-01-05",
        "columns": [{"name": "a", "nulls": 2}, {"name": "b", "nulls": 0}],
    })
    assert compact["columns_with_empty_values"] == {"a": 2}
    assert compact_profile(None) == {}
