"""Verify data/demo/demo_retail_sales.csv against the REAL ingestion stack.

No database credentials, no API keys, no server: the script replays exactly
what the upload route does, in-process (SQLite in-memory + StaticPool, the
same parity engine the test suite uses):

  1. parse_csv_bytes()      - the real CSV parser (encodings, ragged rows;
                               a blank line is skipped by read_csv itself)
  2. clean_dataframe()      - the real cleaning pass (₹/comma stripping,
                               date coercion, duplicate-row drop)
  3. upsert_user_table()    - the real B3 landing step (drop + recreate the
                               per-user table), then COMMIT - as the upload
                               route does - so later rollbacks can't wipe it
  4. execute_sql()          - the REAL query path: INVALID_QUERY sentinel ->
                               sanitize_sql() -> assert_user_scoped() -> run.
                               Demo questions are executed as SQL and checked
                               against ground truth computed independently in
                               pandas, so the CSV provably answers the Loom
                               demo questions. Date windows use
                               substr("date", 1, 10) so the SQLite time-stamp
                               text format can't skew lexicographic compares
                               (on Postgres the pipeline compares the real
                               DATE column directly).
  5. Safety gate            - deletes / cross-table reads / pg_sleep /
                               hallucinated columns / multi-statements /
                               missing LIMIT are all rejected with the right
                               status codes, and the data stays intact.

Run:  cd Backend && python scripts/verify_demo_csv.py
Exit code is 0 only if every check passes.
"""
import asyncio
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pandas as pd  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services.data.executor import (  # noqa: E402
    InvalidQueryError,
    execute_sql,
    user_data_table_name,
)
from app.services.data.parser import (  # noqa: E402
    clean_dataframe,
    parse_csv_bytes,
    upsert_user_table,
)

CSV_PATH = BACKEND_DIR / "data" / "demo" / "demo_retail_sales.csv"
USER_ID = "demouser"
USER_TABLE = user_data_table_name(USER_ID)

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ->  {detail}" if detail else ""))
    (passed if ok else failed).append(name)


async def run_query(session, label: str, query: str) -> list[dict]:
    rows = await execute_sql(query, session, USER_TABLE)
    print(f"    {label}: {rows}")
    return rows


def close(a, b, tol: float = 0.05) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


async def main() -> int:
    print("=" * 74)
    print("STEP 1 - parse + clean the CSV with the real ingestion code")
    print("=" * 74)
    raw = parse_csv_bytes(CSV_PATH.read_bytes())
    df = clean_dataframe(raw)
    print(f"  rows from disk        : {len(raw)} (blank line skipped by read_csv, 1 duplicate row present)")
    check("CSV parses and is not empty", not df.empty, f"{len(df)} rows")
    check(
        "columns normalized to snake_case",
        list(df.columns) == ["date", "category", "channel", "units_sold", "revenue"],
        str(list(df.columns)),
    )
    check(
        "duplicate row silently dropped by the cleaner",
        len(df) == 448,
        f"raw {len(raw)} -> clean {len(df)}",
    )
    dtypes = {c: str(t) for c, t in df.dtypes.items()}
    check("date coerced to datetime", dtypes["date"].startswith("datetime64"), dtypes["date"])
    check("revenue coerced from rupee strings", pd.api.types.is_numeric_dtype(df["revenue"]), dtypes["revenue"])
    check("units_sold coerced from comma strings", pd.api.types.is_numeric_dtype(df["units_sold"]), dtypes["units_sold"])
    print()
    print("  first rows after cleaning (what the upload preview shows):")
    print(df.head(4).to_string(index=False))

    # Ground truth computed independently from the cleaned dataframe.
    max_date = df["date"].max()
    cutoff_last = max_date - pd.Timedelta(days=6)      # first day of the rolling last week
    cutoff_prior = cutoff_last - pd.Timedelta(days=7)
    # Day strings only ("2026-10-24"): pandas Timestamp.isoformat() emits a
    # "T00:00:00" separator while SQLite stores cleaned datetimes as
    # "... 00:00:00.000000" with a space - and " " < "T" lexicographically,
    # which silently drops the boundary day from string comparisons.
    cutoff_last_s, cutoff_prior_s = cutoff_last.date().isoformat(), cutoff_prior.date().isoformat()
    print()
    print(f"  windows: last7 = [{cutoff_last_s} .. {max_date.date()}], prior7 = [{cutoff_prior_s} .. )")
    check("cutoff dates derived as expected",
          cutoff_last_s == (max_date.date() - pd.Timedelta(days=6)).isoformat(),
          f"cutoff_last={cutoff_last_s!r}")

    last7 = df[df["date"] >= cutoff_last]
    prior7 = df[(df["date"] >= cutoff_prior) & (df["date"] < cutoff_last)]
    gt_total = df["revenue"].sum()
    gt_last7 = last7["revenue"].sum()
    gt_prior7 = prior7["revenue"].sum()
    gt_top = df.groupby("category")["revenue"].sum().sort_values(ascending=False)
    gt_last7_channel = last7.groupby("channel")["revenue"].sum().sort_values(ascending=False)
    gt_worst_day = last7.groupby("date")["revenue"].sum().idxmin().strftime("%Y-%m-%d")
    bakery = df[df["category"] == "Bakery"]
    gt_bak_last = bakery[bakery["date"] >= cutoff_last]["revenue"].sum()
    gt_bak_prior = bakery[(bakery["date"] >= cutoff_prior) & (bakery["date"] < cutoff_last)]["revenue"].sum()
    delta = (gt_last7 - gt_prior7) / gt_prior7 * 100
    bak_drop = (gt_bak_last - gt_bak_prior) / gt_bak_prior * 100

    print()
    print("=" * 74)
    print("STEP 2 - ingest with the real upsert_user_table + commit (route parity)")
    print("=" * 74)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    # Date helpers for the SQLite-parity queries: compare the 10-char day
    # prefix. On Postgres the pipeline filters the native DATE column; this
    # only guards the sandbox against timestamp-text suffix skew.
    d = lambda col: f'substr("{col}", 1, 10)'  # noqa: E731

    async with maker() as session:
        table = await upsert_user_table(session, USER_ID, df)
        await session.commit()
        check("per-user table created with the B2<->B3 contract name",
              table == USER_TABLE, table)

        print()
        print("=" * 74)
        print("STEP 3 - demo questions through the real execute_sql() path")
        print("=" * 74)

        # 3.1 grand totals (no window filter)
        rows = await run_query(
            session, "grand totals",
            f"SELECT COUNT(*) AS n, ROUND(SUM(revenue), 2) AS total FROM {USER_TABLE} LIMIT 1",
        )
        check("dataset total matches pandas ground truth",
              rows and rows[0]["n"] == len(df) and close(rows[0]["total"], gt_total),
              f"SQL total {float(rows[0]['total']):,.0f} vs pandas {gt_total:,.0f}")

        # 3.2 last week vs prior week - the "why did revenue change last week?" core
        rows = await run_query(
            session, "last week vs prior week",
            f"SELECT CASE WHEN {d('date')} >= '{cutoff_last_s}' THEN 'last7' ELSE 'prior7' END AS bucket, "
            f"ROUND(SUM(revenue), 2) AS total FROM {USER_TABLE} "
            f"WHERE {d('date')} >= '{cutoff_prior_s}' GROUP BY bucket ORDER BY bucket LIMIT 5",
        )
        got = {r["bucket"]: float(r["total"]) for r in rows}
        ok = close(got.get("last7", 0), gt_last7) and close(got.get("prior7", 0), gt_prior7)
        check("last-7-days vs prior-7-days matches pandas", ok,
              f"SQL last7 {got.get('last7', 0):,.0f} / prior7 {got.get('prior7', 0):,.0f} "
              f"vs pandas {gt_last7:,.0f} / {gt_prior7:,.0f} (story: last week {delta:+.1f}%)")

        # 3.3 top categories
        rows = await run_query(
            session, "top categories",
            f"SELECT category, ROUND(SUM(revenue), 2) AS total FROM {USER_TABLE} "
            f"GROUP BY category ORDER BY total DESC LIMIT 4",
        )
        check("top-2 categories match pandas",
              [r["category"] for r in rows[:2]] == list(gt_top.index[:2]),
              f"SQL top2 {[r['category'] for r in rows[:2]]} vs pandas {[c for c in gt_top.index[:2]]}")

        # 3.4 channel split for last week
        rows = await run_query(
            session, "channel split last week",
            f"SELECT channel, ROUND(SUM(revenue), 2) AS total FROM {USER_TABLE} "
            f"WHERE {d('date')} >= '{cutoff_last_s}' "
            f"GROUP BY channel ORDER BY total DESC LIMIT 5",
        )
        check("channel split for last week matches pandas",
              [r["channel"] for r in rows] == list(gt_last7_channel.index)
              and all(close(r["total"], gt_last7_channel[r["channel"]]) for r in rows),
              f"SQL {[(r['channel'], float(r['total'])) for r in rows]} vs pandas "
              f"{[(c, float(v)) for c, v in gt_last7_channel.items()]}")

        # 3.5 worst day in last week
        rows = await run_query(
            session, "worst day last week",
            f"SELECT {d('date')} AS day, ROUND(SUM(revenue), 2) AS total FROM {USER_TABLE} "
            f"WHERE {d('date')} >= '{cutoff_last_s}' "
            f'GROUP BY day ORDER BY total ASC LIMIT 3',
        )
        got_day = str(rows[0]["day"])[:10] if rows else ""
        check("worst day of last week matches pandas", got_day == gt_worst_day,
              f"SQL {got_day} vs pandas {gt_worst_day}")

        # 3.6 the Bakery collapse - the demo's findable "root cause"
        rows = await run_query(
            session, "bakery collapse",
            f"SELECT ROUND(SUM(CASE WHEN {d('date')} >= '{cutoff_last_s}' THEN revenue ELSE 0 END), 2) AS last7, "
            f"ROUND(SUM(CASE WHEN {d('date')} >= '{cutoff_prior_s}' AND {d('date')} < '{cutoff_last_s}' "
            f"THEN revenue ELSE 0 END), 2) AS prior7 FROM {USER_TABLE} WHERE category = 'Bakery' LIMIT 1",
        )
        bak_ok = close(rows[0]["last7"], gt_bak_last) and close(rows[0]["prior7"], gt_bak_prior)
        check("bakery last-7 vs prior-7 matches pandas", bak_ok,
              f"SQL last7 {float(rows[0]['last7']):,.0f} / prior7 {float(rows[0]['prior7']):,.0f} "
              f"vs pandas {gt_bak_last:,.0f} / {gt_bak_prior:,.0f} (story: Bakery {bak_drop:+.1f}% week over week)")

        print()
        print("=" * 74)
        print("STEP 4 - safety gate: hostile queries must never reach the data")
        print("=" * 74)

        async def rejected(label: str, query: str, want_status: int) -> None:
            try:
                await execute_sql(query, session, USER_TABLE)
                check(label, False, "query was ALLOWED (bug!)")
            except InvalidQueryError:
                check(label, want_status == 400, "INVALID_QUERY sentinel -> InvalidQueryError")
            except HTTPException as exc:
                check(label, exc.status_code == want_status,
                      f"HTTP {exc.status_code} ({exc.detail})")
            except Exception as exc:  # noqa: BLE001
                check(label, False, f"unexpected {type(exc).__name__}: {exc}")

        await rejected("DELETE rejected as forbidden", f"DELETE FROM {USER_TABLE} LIMIT 1", 403)
        await rejected("INSERT rejected as forbidden", f"INSERT INTO {USER_TABLE} (revenue) VALUES (1)", 403)
        await rejected("cross-table read (users) rejected", "SELECT email FROM users LIMIT 1", 403)
        await rejected("pg_sleep rejected", "SELECT pg_sleep(10) LIMIT 1", 403)
        await rejected("hallucinated column -> clean 422", f"SELECT no_such_col FROM {USER_TABLE} LIMIT 1", 422)
        await rejected("multi-statement rejected", "SELECT 1 LIMIT 1; SELECT 2 LIMIT 1", 403)
        await rejected("missing LIMIT rejected", f'SELECT "date" FROM {USER_TABLE}', 403)
        await rejected("INVALID_QUERY sentinel short-circuits", "SELECT 'INVALID_QUERY' LIMIT 1", 400)

        n_after = (await session.execute(text(f"SELECT COUNT(*) AS n FROM {USER_TABLE}"))).scalar()
        check("data intact after all hostile attempts", n_after == len(df),
              f"{n_after} rows (all {len(df)} still there - the 422 rollback never ate user data)")

    await engine.dispose()

    print()
    print("=" * 74)
    print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("failed checks: " + ", ".join(failed))
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
