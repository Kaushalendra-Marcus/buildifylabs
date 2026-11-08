"""Text-to-SQL execution-accuracy eval.

The nightly pipeline eval (eval_pipeline.py) seeds rows directly, so it never
tests the step that most often produces a confident wrong answer: the generated
SQL. This eval does. For each golden case it

1. loads a small fixture table (evals/sql_golden.jsonl) as the per-user table,
2. builds the *same* schema + data-profile prompt production sends,
3. asks the real model for SQL, runs it through the real sanitizer/scoping
   executor, and
4. compares the RESULT (not the SQL text) with the expected rows, ignoring
   column order, aliases and row order unless the case says otherwise.

A case with ``expect_invalid`` passes only if the model abstains with the
INVALID_QUERY sentinel instead of inventing a query for data that isn't there.

Usage:
    python scripts/eval_sql.py --dry-run          # no keys: verifies the fixtures
                                                  # (reference SQL == expected rows)
    GROQ_API_KEY=... python scripts/eval_sql.py   # live model, SQLite fixture DB
    GROQ_API_KEY=... python scripts/eval_sql.py --repeat 3
    EVAL_DATABASE_URL=postgresql+asyncpg://... python scripts/eval_sql.py
                                                  # run against real Postgres
                                                  # (creates/drops user_eval_data)

Live runs on SQLite skip ``postgres_only`` cases (the model writes Postgres date
arithmetic); point EVAL_DATABASE_URL at a scratch Postgres to include them.
Results: evals/results/sql-eval-<ts>.jsonl. Exits non-zero on any failure.
Keep out of CI (live keys); the offline fixture check runs in pytest.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "eval-secret")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "1025")
os.environ.setdefault("SMTP_USER", "eval")
os.environ.setdefault("SMTP_PASS", "eval")
os.environ.setdefault("EMAIL_FROM", "eval@example.com")
os.environ.setdefault("CONTACT_FORM_RECIPIENT_EMAIL", "eval@example.com")
os.environ.setdefault("GOOGLE_CLIENT_ID", "eval")
if "--dry-run" in sys.argv:
    # Offline check never calls a model, but app.config requires a model name.
    # (Not set for live runs: that would shadow GROQ_MODEL from Backend/.env.)
    os.environ.setdefault("GROQ_MODEL", "dry-run-model")
    os.environ.setdefault("GROQ_API_KEY", "dry-run-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services.data.executor import (  # noqa: E402
    execute_sql,
    get_data_profile,
    get_table_columns,
    user_data_table_name,
)
from app.services.llm.sql_generator import (  # noqa: E402
    SQL_SYSTEM_PROMPT,
    build_data_schema,
    build_sql_prompt,
    clean_sql_response,
    is_invalid_query,
)

_HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = _HERE.parent / "evals" / "sql_golden.jsonl"
DEFAULT_OUT_DIR = _HERE.parent / "evals" / "results"
USER_ID = "eval"
TABLE = user_data_table_name(USER_ID)


# ---------------------------------------------------------------------------
# Golden file
# ---------------------------------------------------------------------------

def load_cases(path):
    """Return (fixtures_by_name, cases). Fails loudly on a malformed file."""
    fixtures, cases = {}, []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}")
            if "fixture_def" in item:
                fx = item["fixture_def"]
                for key in ("name", "columns", "rows"):
                    if key not in fx:
                        raise SystemExit(f"{path}:{lineno}: fixture_def missing {key!r}")
                for row in fx["rows"]:
                    if len(row) != len(fx["columns"]):
                        raise SystemExit(f"{path}:{lineno}: fixture row width mismatch")
                fixtures[fx["name"]] = fx
                continue
            for key in ("id", "fixture", "question"):
                if key not in item:
                    raise SystemExit(f"{path}:{lineno}: case missing {key!r}")
            if not item.get("expect_invalid"):
                for key in ("reference_sql", "expected"):
                    if key not in item:
                        raise SystemExit(f"{path}:{lineno} ({item['id']}): missing {key!r}")
            cases.append(item)
    ids = [c["id"] for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SystemExit(f"{path}: duplicate case ids: {dupes}")
    for case in cases:
        if case["fixture"] not in fixtures:
            raise SystemExit(f"{path}: case {case['id']} references unknown fixture")
    return fixtures, cases


# ---------------------------------------------------------------------------
# Fixture database
# ---------------------------------------------------------------------------

def _sql_type(values):
    cleaned = [v for v in values if v is not None]
    if cleaned and all(isinstance(v, int) and not isinstance(v, bool) for v in cleaned):
        return "INTEGER"
    if cleaned and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cleaned):
        return "REAL"
    return "TEXT"


def make_engine(database_url=None):
    if database_url:
        return create_async_engine(database_url)
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def load_fixture(engine, fixture):
    columns, rows = fixture["columns"], fixture["rows"]
    types = [_sql_type([row[i] for row in rows]) for i in range(len(columns))]
    ddl = ", ".join(f'"{c}" {t}' for c, t in zip(columns, types))
    insert = text(
        f'INSERT INTO "{TABLE}" (' + ", ".join(f'"{c}"' for c in columns) + ") VALUES ("
        + ", ".join(f":p{i}" for i in range(len(columns))) + ")"
    )
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{TABLE}"'))
        await conn.execute(text(f'CREATE TABLE "{TABLE}" ({ddl})'))
        await conn.execute(
            insert, [{f"p{i}": v for i, v in enumerate(row)} for row in rows]
        )


async def drop_fixture(engine):
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{TABLE}"'))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Result comparison (column order / aliases / row order insensitive)
# ---------------------------------------------------------------------------

def _canon(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return str(value).strip().lower()


def _cell_eq(a, b, tol):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(1e-6, abs(b) * tol)
    return a == b


def _cells_match(actual_cells, expected_cells, tol, subset):
    remaining = list(actual_cells)
    for expected in expected_cells:
        for index, actual in enumerate(remaining):
            if _cell_eq(actual, expected, tol):
                remaining.pop(index)
                break
        else:
            return False
    return True if subset else not remaining


def rows_match(actual_rows, expected, default_tol=0.01):
    """Compare an executed result with a case's ``expected`` block."""
    tol = float(expected.get("tolerance", default_tol))
    subset = expected.get("mode", "exact") == "subset"
    ordered = bool(expected.get("ordered", False))
    actual = [[_canon(v) for v in dict(row).values()] for row in actual_rows]
    wanted = [[_canon(v) for v in row] for row in expected["rows"]]
    if len(actual) != len(wanted):
        return False
    if ordered:
        return all(_cells_match(a, w, tol, subset) for a, w in zip(actual, wanted))
    used = set()
    for want in wanted:
        for index, got in enumerate(actual):
            if index not in used and _cells_match(got, want, tol, subset):
                used.add(index)
                break
        else:
            return False
    return True


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

async def dry_run_all(fixtures, cases, engine=None):
    """Verify every fixture offline: reference SQL must reproduce ``expected``.

    Returns a list of failure strings (empty == all fixtures consistent).
    """
    owned = engine is None
    engine = engine or make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    failures = []
    try:
        loaded = None
        for case in cases:
            if case.get("expect_invalid"):
                continue
            if loaded != case["fixture"]:
                await load_fixture(engine, fixtures[case["fixture"]])
                loaded = case["fixture"]
            sql = case["reference_sql"].replace("{table}", TABLE)
            async with sessions() as session:
                try:
                    rows = await execute_sql(sql, session, TABLE)
                except Exception as exc:  # HTTPException / SQL error
                    failures.append(f"{case['id']}: reference SQL failed: {exc}")
                    continue
            if not rows_match(rows, case["expected"]):
                failures.append(f"{case['id']}: reference result {rows} != expected {case['expected']['rows']}")
    finally:
        await drop_fixture(engine)
        if owned:
            await engine.dispose()
    return failures


async def _generate_sql(question, session, columns, profile):
    from app.services.llm.groq_service import generate_response

    schema = build_data_schema(TABLE, columns, profile or None)
    result = await generate_response(
        prompt=build_sql_prompt(question, schema),
        system_prompt=SQL_SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=512,
    )
    return clean_sql_response(result.get("content") or "")


async def live_main(fixtures, cases, out_dir, database_url):
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(out_dir, f"sql-eval-{stamp}.jsonl")
    engine = make_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    is_sqlite = engine.dialect.name == "sqlite"
    per_case = {}
    passed = failed = skipped = 0
    try:
        loaded = None
        with open(out_path, "w", encoding="utf-8") as fh:
            for case in cases:
                if case.get("postgres_only") and is_sqlite:
                    skipped += 1
                    print(f"[SKIP] {case['id']} (postgres_only; set EVAL_DATABASE_URL)")
                    continue
                if loaded != case["fixture"]:
                    await load_fixture(engine, fixtures[case["fixture"]])
                    loaded = case["fixture"]
                record = {"id": case["id"], "question": case["question"], "passed": False}
                try:
                    async with sessions() as session:
                        columns = await get_table_columns(session, TABLE)
                        profile = await get_data_profile(session, TABLE, columns)
                        sql = await _generate_sql(case["question"], session, columns, profile)
                        record["sql"] = sql
                        if case.get("expect_invalid"):
                            record["passed"] = is_invalid_query(sql)
                            if not record["passed"]:
                                record["failure"] = "model invented a query instead of abstaining"
                        else:
                            rows = await execute_sql(sql, session, TABLE)
                            record["rows"] = rows[:10]
                            record["passed"] = rows_match(rows, case["expected"])
                            if not record["passed"]:
                                record["failure"] = f"result != expected {case['expected']['rows']}"
                except Exception as exc:
                    record["failure"] = f"{type(exc).__name__}: {exc}"
                fh.write(json.dumps(record, default=str) + "\n")
                per_case.setdefault(case["id"], []).append(record["passed"])
                if record["passed"]:
                    passed += 1
                    print(f"[PASS] {case['id']}")
                else:
                    failed += 1
                    print(f"[FAIL] {case['id']} - {record.get('failure')}\n       sql: {record.get('sql')}")
    finally:
        await drop_fixture(engine)
        await engine.dispose()
    total = passed + failed
    print(f"\nWrote results to {out_path}")
    if max((len(r) for r in per_case.values()), default=1) > 1:
        print("== Pass rate per case ==")
        for case_id, runs in sorted(per_case.items()):
            print(f"  {case_id}: {sum(runs)}/{len(runs)}")
    accuracy = (passed / total * 100) if total else 0.0
    print(f"EXECUTION ACCURACY {accuracy:.0f}% ({passed}/{total}; {skipped} skipped)")
    return 0 if failed == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Text-to-SQL execution-accuracy eval.")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--case", default=None, help="run one case id only")
    parser.add_argument("--repeat", type=int, default=1, help="run each case N times")
    parser.add_argument("--list", action="store_true", help="list case ids and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="verify fixtures offline (reference SQL == expected); no model")
    args = parser.parse_args(argv)
    fixtures, cases = load_cases(args.golden)
    if args.list:
        for case in cases:
            print(f"{case['id']}\t{case['question']}")
        return 0
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if not cases:
        print("No cases selected.")
        return 1
    if args.dry_run:
        failures = asyncio.run(dry_run_all(fixtures, cases))
        for failure in failures:
            print(f"[FAIL] {failure}")
        print("DRY-RUN " + ("PASSED" if not failures else "FAILED"))
        return 0 if not failures else 1
    if not os.environ.get("GROQ_API_KEY"):
        # Same convenience as eval_pipeline.py: accept the key from Backend/.env.
        try:
            for line in (_HERE.parent / ".env").read_text().splitlines():
                if line.strip().startswith("GROQ_API_KEY") and "=" in line:
                    value = line.partition("=")[2].strip().strip("\"'")
                    if value:
                        os.environ.setdefault("GROQ_API_KEY", value)
        except OSError:
            pass
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is required for the live eval. Use --dry-run for the offline fixture check.")
        return 2
    if args.repeat and args.repeat > 1:
        cases = [case for case in cases for _ in range(args.repeat)]
    return asyncio.run(
        live_main(fixtures, cases, args.out_dir, os.environ.get("EVAL_DATABASE_URL"))
    )


if __name__ == "__main__":
    raise SystemExit(main())
