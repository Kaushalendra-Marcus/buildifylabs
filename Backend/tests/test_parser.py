"""Parser + ingestion (specs/04 §5-§6): CSV -> defensive clean -> per-user data
table that B2's SQL layer can query (via the exact user_data_table_name()).

Covers ragged/empty files, encoding fallback, column normalization, dedup,
date + Indian-currency coercion, typed columns, and the one-file-at-a-time
"a fresh upload replaces the user's data table" behavior.

No pytest-asyncio needed - each async scenario is driven with asyncio.run,
matching the existing suite's pattern.
"""

import asyncio

import pytest
from pandas.api.types import is_datetime64_any_dtype
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.data.executor import user_data_table_name
from app.services.data.parser import (
    clean_dataframe,
    ingest_file,
    parse_csv_bytes,
)

USER_ID = "30303030-1111-2222-3333-444455556666"
USER_TABLE = user_data_table_name(USER_ID)


def run(coro):
    return asyncio.run(coro)


def is_datetime(col):
    return is_datetime64_any_dtype(col)


class TestParseCSV:
    def test_plain_rows(self):
        df = parse_csv_bytes(b"a,b\n1,2\n3,4\n")
        assert df.to_dict("records") == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def test_utf8_bom_tolerated(self):
        df = parse_csv_bytes(b"\xef\xbb\xbfa,b\n1,2\n")
        assert list(df.columns) == ["a", "b"]
        assert len(df) == 1

    def test_latin1_encoding_fallback(self):
        df = parse_csv_bytes("name\nJosé\n".encode("latin-1"))
        assert df.iloc[0]["name"] == "José"

    def test_ragged_row_does_not_crash(self):
        df = parse_csv_bytes(b"a,b,c\n1,2,3\n4,5\n6,7,8\n")
        assert len(df) >= 2

    def test_empty_csv_raises(self):
        with pytest.raises(ValueError):
            parse_csv_bytes(b"")


class TestCleaning:
    CSV = (
        "Order Date,Region,Amount,Paid?\n"
        "2024-01-01,East,\"\u20b91,200\",true\n"
        "2024-01-02,West,400,false\n"
        "2024-01-01,East,\"\u20b91,200\",true\n"
    ).encode("utf-8")

    def test_columns_normalized_to_snake(self):
        df = clean_dataframe(parse_csv_bytes(self.CSV))
        assert list(df.columns) == ["order_date", "region", "amount", "paid"]

    def test_duplicate_row_removed(self):
        df = clean_dataframe(parse_csv_bytes(self.CSV))
        assert len(df) == 2

    def test_currency_column_coerced(self):
        df = clean_dataframe(parse_csv_bytes(self.CSV))
        amounts = sorted(float(v) for v in df["amount"].dropna().tolist())
        assert amounts == [400.0, 1200.0]

    def test_date_column_coerced(self):
        df = clean_dataframe(parse_csv_bytes(self.CSV))
        assert is_datetime(df["order_date"])

    def test_all_empty_row_dropped(self):
        df = clean_dataframe(parse_csv_bytes(b"a,b,c\n1,2,3\n,,\n4,5,6\n"))
        assert len(df) == 2

    def test_header_only_is_empty_after_clean(self):
        assert clean_dataframe(parse_csv_bytes(b"a,b\n")).empty


class TestIngest:
    def _ingest(self, content, filename="sales.csv"):
        async def go():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    return await ingest_file(session, USER_ID, filename, content)
            finally:
                await engine.dispose()

        return run(go())

    def test_lands_a_queryable_per_user_table(self):
        csv = (
            b"date,revenue,region\n"
            b"2024-01-01,1200,east\n"
            b"2024-01-02,800,west\n"
        )
        async def verify():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    table = await ingest_file(session, USER_ID, "sales.csv", csv)
                    assert table == USER_TABLE
                    result = await session.execute(
                        text(f'SELECT revenue FROM "{table}" ORDER BY revenue')
                    )
                    assert [r["revenue"] for r in result.mappings()] == [800, 1200]
            finally:
                await engine.dispose()

        run(verify())

    def test_second_upload_replaces_the_users_table(self):
        async def scenario():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    await ingest_file(
                        session, USER_ID, "first.csv", b"a,b\n1,2\n3,4\n"
                    )
                    await ingest_file(
                        session, USER_ID, "second.csv", b"a,b\n9,9\n"
                    )
                    result = await session.execute(
                        text(f"SELECT COUNT(*) AS n FROM {USER_TABLE}")
                    )
                    assert result.mappings().one()["n"] == 1
            finally:
                await engine.dispose()

        run(scenario())

    def test_header_only_csv_raises_no_data_rows(self):
        with pytest.raises(ValueError, match="no data rows"):
            self._ingest(b"a,b\n")

    def test_xlsx_upload_raises_unsupported_reason(self):
        with pytest.raises(ValueError, match="not supported yet"):
            self._ingest(b"PK\x03\x04\x00", filename="data.xlsx")