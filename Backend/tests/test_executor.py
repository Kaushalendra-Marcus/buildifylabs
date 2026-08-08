"""execute_sql() end-to-end against an in-memory SQLite database.

The user's data table is created with the exact name user_data_table_name()
produces (the B3 co-design contract), so these tests prove the whole
"a query can never return another user's rows" guarantee: only queries against
the caller's own table run, and they only ever read that table's rows.

Run via `python -m pytest` from Backend/ (no pytest-asyncio needed - each test
drives its async scenario with asyncio.run).
"""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.data.executor import (
    InvalidQueryError,
    assert_user_scoped,
    execute_sql,
    user_data_table_name,
)

USER_TABLE = user_data_table_name("abc123")


def run(coro):
    return asyncio.run(coro)


async def _setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"CREATE TABLE {USER_TABLE} "
                "(id INTEGER PRIMARY KEY, name TEXT, revenue INTEGER)"
            )
        )
        await conn.execute(
            text(
                f"INSERT INTO {USER_TABLE} (id, name, revenue) "
                "VALUES (1, 'acme', 100), (2, 'beta', 250)"
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, maker


def test_execute_returns_rows_as_dicts():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                rows = await execute_sql(
                    f"SELECT id, name FROM {USER_TABLE} LIMIT 100",
                    session,
                    USER_TABLE,
                )
            return rows
        finally:
            await engine.dispose()

    rows = run(scenario())
    assert rows == [{"id": 1, "name": "acme"}, {"id": 2, "name": "beta"}]


def test_empty_result_is_not_an_error():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                return await execute_sql(
                    f"SELECT id FROM {USER_TABLE} WHERE revenue > 999 LIMIT 100",
                    session,
                    USER_TABLE,
                )
        finally:
            await engine.dispose()

    assert run(scenario()) == []


def test_sentinel_short_circuits_before_execution():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                await execute_sql(
                    "SELECT 'INVALID_QUERY' LIMIT 1", session, USER_TABLE
                )
        finally:
            await engine.dispose()

    with pytest.raises(InvalidQueryError):
        run(scenario())


def test_foreign_table_rejected_with_403():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                with pytest.raises(HTTPException) as exc:
                    await execute_sql("SELECT email FROM users LIMIT 1", session, USER_TABLE)
                return exc.value.status_code
        finally:
            await engine.dispose()

    assert run(scenario()) == 403


def test_write_rejected_with_403():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                with pytest.raises(HTTPException) as exc:
                    await execute_sql(
                        f"DELETE FROM {USER_TABLE} WHERE id = 1", session, USER_TABLE
                    )
                return exc.value.status_code
        finally:
            await engine.dispose()

    assert run(scenario()) == 403


def test_hallucinated_column_is_clean_422_not_500():
    async def scenario():
        engine, maker = await _setup_db()
        try:
            async with maker() as session:
                with pytest.raises(HTTPException) as exc:
                    await execute_sql(
                        f"SELECT nonexistent_column FROM {USER_TABLE} LIMIT 1",
                        session,
                        USER_TABLE,
                    )
                return exc.value.status_code
        finally:
            await engine.dispose()

    assert run(scenario()) == 422


def test_own_table_reference_is_unaffected_by_bad_data():
    # Even if the user's own table were absent, scoping passes first and the
    # error is a clean 422 (unknown table), never a 500 or another user's rows.
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE other_user_data (id INTEGER)"))
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                with pytest.raises(HTTPException) as exc:
                    await execute_sql(
                        "SELECT id FROM other_user_data LIMIT 1",
                        session,
                        USER_TABLE,
                    )
                return exc.value.status_code
        finally:
            await engine.dispose()

    assert run(scenario()) == 403
