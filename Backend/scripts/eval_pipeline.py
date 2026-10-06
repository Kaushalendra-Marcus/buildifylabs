"""Tier-B nightly eval: golden cases through the real pipeline.

Runs Backend/evals/golden.jsonl through `run_pipeline` with the REAL Groq
model (live keys required) and scores each answer with DETERMINISTIC
property checks only (no LLM judge, no new dependencies):

- clarification / confidence contract (clarify, clarify_or_lowconf, caps)
- gate contract (blocked_expected, partial_expected via research_state)
- visual contract (no_chart, want_visual_type, min_visuals, no_forecast,
  no_scenario, excluded_entities absent from visuals)
- citation contract (every [n] resolves to a listed snippet)
- number contract (every non-year number in prose + visuals traces to
  rows/computed/snippets/series within 1%)
- disclosure contract (excluded entities named in prose, must_contain in
  prose, must_not_contain absent)

Usage:
    GROQ_API_KEY=... python scripts/eval_pipeline.py
    GROQ_API_KEY=... python scripts/eval_pipeline.py --only web
    GROQ_API_KEY=... python scripts/eval_pipeline.py --case web-02-partial-3y
    python scripts/eval_pipeline.py --dry-run   # no keys, no LLM: validates
                                                # the golden file + evidence
                                                # wiring + local gates only
    python scripts/eval_pipeline.py --list      # list case ids, no keys needed

Results: one JSON object per line in Backend/evals/results/eval-<ts>.jsonl
(gitignored). Exits non-zero when any case fails. Blocked-gate charts are a
hard failure. Keep out of CI (needs live keys + flaky vendors); run nightly.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Dummy env: importing app.config validates these, but only Groq keys must
# be real. Mirrors the convention used by the repo's test conftest.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.data.stats import (  # noqa: E402
    apply_what_if,
    compute_forecast,
    compute_statistics,
    infer_forecast_columns,
    is_forecast_query,
    parse_what_if,
)
from app.services.llm.langchain_pipeline import run_pipeline  # noqa: E402
from app.services.llm.pipeline.grounding import _parse_scaled_number  # noqa: E402

_HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = _HERE.parent / "evals" / "golden.jsonl"
DEFAULT_OUT_DIR = _HERE.parent / "evals" / "results"

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\$?\d[\d,]*(?:\.\d+)?")
_CITATION_RE = re.compile(r"\[(\d+)\]")

# Expect keys the runner understands (unknown keys fail loading: a golden
# file that silently ignores assertions is worse than no golden file).
KNOWN_EXPECT_KEYS = frozenset({
    "clarify", "clarify_or_lowconf", "blocked_expected", "partial_expected",
    "max_confidence", "min_confidence", "must_contain", "must_not_contain",
    "no_chart", "want_visual_type", "min_visuals", "citations_resolve",
    "no_invented_numbers", "excluded_entities", "no_forecast", "no_scenario",
    "forecast_computable", "answer_nonempty",
})


def ensure_live_keys():
    """Accept keys from the environment or the local Backend/.env file.

    Nightly runs execute from Backend/ where .env holds the real Groq keys
    but those may not be exported into the shell — pydantic reads them via
    env_file only after import. Seed os.environ from .env first so the guard
    below sees the same keys.
    """
    if os.environ.get("GROQ_API_KEY"):
        return True
    dotenv = _HERE.parent / ".env"
    try:
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line.startswith("GROQ_API_KEY") and "=" in line:
                _, _, value = line.partition("=")
                value = value.strip().strip("\"'")
                if value:
                    os.environ.setdefault("GROQ_API_KEY", value)
                    return True
    except OSError:
        pass
    return bool(os.environ.get("GROQ_API_KEY"))


def load_golden(path):
    cases = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                case = json.loads(line)
            except ValueError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}")
            for key in ("id", "query", "source_scope", "seed", "expect"):
                if key not in case:
                    raise SystemExit(f"{path}:{lineno}: case missing {key!r}")
            unknown = set(case.get("expect", {})) - KNOWN_EXPECT_KEYS
            if unknown:
                raise SystemExit(
                    f"{path}:{lineno} ({case.get('id')}): unknown expect "
                    f"keys {sorted(unknown)} (runner would ignore them)"
                )
            if case["source_scope"] not in ("own_data", "live_web", "both"):
                raise SystemExit(
                    f"{path}:{lineno} ({case.get('id')}): bad source_scope"
                )
            cases.append(case)
    ids = [c["id"] for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SystemExit(f"{path}: duplicate case ids: {dupes}")
    return cases


def _answer_numbers(text):
    """Floats in prose. Citation markers are stripped first (a [2] is a
    pointer, not a claim); year-like ints (1900-2100) are labels, not claims
    — same discipline as the CI golden tests."""
    out = []
    scrubbed = _CITATION_RE.sub(" ", text or "")
    for match in _NUMBER_RE.finditer(scrubbed):
        raw = match.group(0).replace("$", "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if 1900 <= value <= 2100 and value.is_integer():
            continue
        if len(raw) == 4 and raw.startswith(("19", "20")):
            continue
        out.append(value)
    return out


def _flatten(obj):
    found = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(_flatten(value))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found.extend(_flatten(value))
    return found


def _visual_numbers(visuals):
    found = []

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    for visual in visuals or []:
        props = visual.props if hasattr(visual, "props") else visual.get("props", {})
        walk(props or {})
    return found


def evidence_numbers(seed, computed):
    """Every validated numeric value the answer may legitimately reuse:
    rows (incl. prior-turn rows), deterministic computed stats, snippet
    figures, and structured series values."""
    pool = []
    prior_rows = ((seed.get("prior_data", {}) or {}).get("rows", []) or [])
    for row in (seed.get("rows", []) or []) + prior_rows:
        if isinstance(row, dict):
            pool.extend(
                float(v) for v in row.values() if isinstance(v, (int, float))
            )
    pool.extend(_flatten(computed or {}))
    for snippet in seed.get("snippets", []) or []:
        try:
            pool.extend(_parse_scaled_number(str(snippet)))
        except Exception:
            pass
    for item in (seed.get("price_history", []) or []) + (
        seed.get("market_data", []) or []
    ):
        if isinstance(item, dict):
            pool.extend(
                float(v)
                for v in (item.get("values") or [])
                if isinstance(v, (int, float))
            )
    for item in seed.get("financial_history", []) or []:
        if not isinstance(item, dict):
            continue
        for block_key in ("revenue", "net_income"):
            block = item.get(block_key, {}) or {}
            if isinstance(block, dict):
                pool.extend(
                    float(v)
                    for v in (block.get("values") or [])
                    if isinstance(v, (int, float))
                )
    for item in seed.get("fundamentals", []) or []:
        pool.extend(_flatten(item))
    return pool


def precompute_numbers(query, rows):
    """Mirror app/routes/chat.py: deterministic stats always, then the
    row-level what-if and forecast when the query asks for them.

    run_pipeline narrates precomputed numbers only; the eval must feed it
    the same computed_numbers the route would, or forecast/scenario cases
    measure the wrong thing.
    """
    computed = compute_statistics(rows) if rows else {}
    if rows:
        try:
            scenario = parse_what_if(query)
            if scenario is not None:
                what_if = apply_what_if(rows, *scenario)
                if what_if is not None:
                    computed = {**computed, "what_if": what_if}
        except Exception:
            pass
        try:
            if is_forecast_query(query):
                date_col, value_col = infer_forecast_columns(rows)
                if date_col and value_col:
                    forecast = compute_forecast(rows, date_col, value_col)
                    if forecast is not None:
                        computed = {**computed, "forecast": forecast}
        except Exception:
            pass
    return computed


def check_case(case, output, computed):
    """Deterministic property checks. Returns a list of failure strings."""
    failures = []
    seed = case.get("seed", {}) or {}
    expect = case.get("expect", {}) or {}
    answer = output.answer or ""
    lowered = answer.lower()
    visuals = list(output.visuals or [])
    kinds = [getattr(v, "visual_type", "") for v in visuals]
    research_state = output.research_state or {}
    gate = research_state.get("gate", {}) or {}
    snippets = seed.get("snippets", []) or []
    visuals_blob = json.dumps(
        [getattr(v, "props", None) or {} for v in visuals], default=str
    )
    blob = (answer + " " + visuals_blob).lower()

    if "clarify" in expect:
        want = bool(expect["clarify"])
        got = output.clarification is not None
        if want != got:
            failures.append(
                f"clarify: expected clarification={want}, got {got}"
            )

    if expect.get("clarify_or_lowconf"):
        try:
            low = float(output.confidence or 0.0) <= 0.35
        except (TypeError, ValueError):
            low = True
        if output.clarification is None and not low:
            failures.append(
                f"clarify_or_lowconf: no clarification and confidence "
                f"{output.confidence} > 0.35"
            )

    if "blocked_expected" in expect:
        want = bool(expect["blocked_expected"])
        got = bool(gate.get("blocked"))
        if want != got:
            failures.append(
                f"blocked_expected: research_state gate blocked={got}, "
                f"expected {want}"
            )

    if expect.get("partial_expected") and not gate.get("partial"):
        failures.append("partial_expected: research_state gate partial is not true")

    if "max_confidence" in expect:
        try:
            conf = float(output.confidence)
        except (TypeError, ValueError):
            failures.append(
                f"max_confidence: confidence {output.confidence!r} not numeric"
            )
            conf = None
        if conf is not None and conf > float(expect["max_confidence"]) + 1e-9:
            failures.append(
                f"max_confidence: {conf} exceeds {expect['max_confidence']}"
            )

    if "min_confidence" in expect:
        try:
            conf = float(output.confidence)
        except (TypeError, ValueError):
            failures.append(
                f"min_confidence: confidence {output.confidence!r} not numeric"
            )
            conf = None
        if conf is not None and conf < float(expect["min_confidence"]) - 1e-9:
            failures.append(
                f"min_confidence: {conf} below {expect['min_confidence']}"
            )

    for phrase in expect.get("must_contain", []) or []:
        if str(phrase).lower() not in lowered:
            failures.append(f"must_contain: {phrase!r} not in answer")

    for phrase in expect.get("must_not_contain", []) or []:
        if str(phrase).lower() in blob:
            failures.append(f"must_not_contain: {phrase!r} leaked into answer/visuals")

    if expect.get("no_chart"):
        bad = [k for k in kinds if k in ("graph", "comparison")]
        if bad:
            failures.append(f"no_chart: chart visuals shipped: {bad}")

    if expect.get("want_visual_type"):
        if expect["want_visual_type"] not in kinds:
            failures.append(
                f"want_visual_type: no {expect['want_visual_type']} visual "
                f"(got {kinds})"
            )

    if "min_visuals" in expect and len(visuals) < int(expect["min_visuals"]):
        failures.append(
            f"min_visuals: {len(visuals)} < {expect['min_visuals']}"
        )

    if expect.get("citations_resolve"):
        cited = set()
        for match in _CITATION_RE.finditer(answer):
            raw = match.group(1)
            if len(raw) >= 4:  # years are never citations
                continue
            try:
                cited.add(int(raw))
            except ValueError:
                pass
        bad = sorted(n for n in cited if n < 1 or n > len(snippets))
        if bad:
            failures.append(
                f"citations_resolve: markers {bad} point outside "
                f"{len(snippets)} snippet(s)"
            )

    if expect.get("no_invented_numbers"):
        if output.clarification is not None:
            pass  # a clarification makes no numeric claims to ground
        else:
            pool = evidence_numbers(seed, computed)
            if not pool:
                failures.append(
                    "no_invented_numbers: empty evidence pool (case needs "
                    "numeric seeds to check grounding)"
                )
            else:
                claimed = _answer_numbers(answer) + _visual_numbers(visuals)
                for value in claimed:
                    if not any(
                        abs(c - value) <= max(1e-6, abs(value) * 0.01)
                        for c in pool
                    ):
                        failures.append(f"no_invented_numbers: {value} untraceable")
                        break

    for entity in expect.get("excluded_entities", []) or []:
        if str(entity).lower() not in lowered:
            failures.append(
                f"excluded_entities: {entity!r} missing from answer "
                f"(exclusion undisclosed)"
            )
        if str(entity).lower() in visuals_blob.lower():
            failures.append(
                f"excluded_entities: {entity!r} present in a visual"
            )

    if expect.get("no_forecast"):
        titles = " ".join(str(getattr(v, "title", "") or "") for v in visuals)
        if (
            "project" in lowered
            or "project" in visuals_blob.lower()
            or "forecast" in (titles.lower() + visuals_blob.lower())
        ):
            failures.append("no_forecast: projected numbers or visuals emitted")

    if expect.get("no_scenario"):
        prov_blob = json.dumps(
            [getattr(v, "provenance", None) or {} for v in visuals],
            default=str,
        ).lower()
        titles = " ".join(str(getattr(v, "title", "") or "") for v in visuals)
        if (
            "what_if" in prov_blob
            or "scenario" in (titles.lower() + prov_blob)
            or "scenario_total" in lowered
        ):
            failures.append("no_scenario: scenario math or visuals emitted")

    if expect.get("forecast_computable"):
        rows = seed.get("rows", []) or []
        ok = False
        try:
            if is_forecast_query(case["query"]):
                date_col, value_col = infer_forecast_columns(rows)
                ok = (
                    bool(date_col and value_col)
                    and compute_forecast(rows, date_col, value_col) is not None
                )
        except Exception:
            ok = False
        if not ok:
            failures.append(
                "forecast_computable: deterministic forecast gate produced nothing"
            )

    if expect.get("answer_nonempty") and not answer.strip():
        failures.append("answer_nonempty: empty answer with no clarification")

    return failures


async def run_case(case):
    seed = case.get("seed", {}) or {}
    rows = list(seed.get("rows", []) or [])
    computed = precompute_numbers(case["query"], rows)
    try:
        from app.services import web_search_cache

        web_search_cache._reset_cache_state()
    except Exception:
        pass
    output = await run_pipeline(
        user_query=case["query"],
        db_data=rows,
        computed_numbers=computed,
        news_context=list(seed.get("snippets", []) or []),
        source_scope=case.get("source_scope", "own_data"),
        prior_clarification=seed.get("prior_clarification"),
        prior_data=seed.get("prior_data"),
        market_data=list(seed.get("market_data", []) or []),
        web_sources=list(seed.get("sources", []) or []),
        fundamentals=list(seed.get("fundamentals", []) or []),
        price_history=list(seed.get("price_history", []) or []),
        financial_history=list(seed.get("financial_history", []) or []),
        documents_scoped=bool(seed.get("documents_scoped", False)),
    )
    try:
        from app.services import web_search_cache

        web_search_cache._reset_cache_state()
    except Exception:
        pass
    return output, computed


def dry_run(cases):
    """No keys, no LLM: validate the file + evidence wiring + local gates."""
    print(f"dry-run: {len(cases)} cases loaded, no LLM calls made")
    ok = True
    for case in cases:
        seed = case.get("seed", {}) or {}
        expect = case.get("expect", {}) or {}
        rows = list(seed.get("rows", []) or [])
        problems = []
        if expect.get("no_invented_numbers"):
            computed = precompute_numbers(case["query"], rows)
            if not evidence_numbers(seed, computed):
                problems.append("empty evidence pool (no numeric seeds)")
        if expect.get("forecast_computable"):
            try:
                date_col, value_col = infer_forecast_columns(rows)
                if not (date_col and value_col) or compute_forecast(
                    rows, date_col, value_col
                ) is None:
                    problems.append("local forecast gate yields nothing")
            except Exception as exc:
                problems.append(f"forecast gate raised: {exc}")
        if expect.get("excluded_entities") and not (
            seed.get("price_history") or seed.get("financial_history")
        ):
            problems.append("excluded_entities without history seeds")
        status = "OK  " if not problems else "FAIL"
        if problems:
            ok = False
        print(f"[{status}] {case['id']}" + (f" — {'; '.join(problems)}" if problems else ""))
    print("DRY-RUN " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


async def live_main(cases, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(out_dir, f"eval-{stamp}.jsonl")
    passed = failed = 0
    component_stats = {}
    with open(out_path, "w", encoding="utf-8") as fh:
        for case in cases:
            comp = case["id"].split("-")[0]
            stats = component_stats.setdefault(comp, {"pass": 0, "fail": 0})
            try:
                output, computed = await run_case(case)
                failures = check_case(case, output, computed)
                record = {
                    "id": case["id"],
                    "query": case["query"],
                    "source_scope": case.get("source_scope"),
                    "passed": not failures,
                    "failures": failures,
                    "confidence": output.confidence,
                    "clarification": bool(output.clarification is not None),
                    "visual_types": [
                        getattr(v, "visual_type", "") for v in (output.visuals or [])
                    ],
                    "gate": (output.research_state or {}).get("gate", {}),
                    "answer_excerpt": (output.answer or "")[:300],
                }
            except Exception as exc:  # never let one case kill the night
                record = {
                    "id": case["id"],
                    "query": case["query"],
                    "source_scope": case.get("source_scope"),
                    "passed": False,
                    "failures": [f"eval harness error: {type(exc).__name__}: {exc}"],
                    "confidence": None,
                    "clarification": None,
                    "visual_types": [],
                    "gate": {},
                    "answer_excerpt": "",
                }
            fh.write(json.dumps(record, default=str) + "\n")
            if record["failures"]:
                failed += 1
                stats["fail"] += 1
                print(f"[FAIL] {case['id']} — {'; '.join(record['failures'])}")
            else:
                passed += 1
                stats["pass"] += 1
                print(f"[PASS] {case['id']}")
    print(f"\nWrote {passed + failed} records to {out_path}")
    print("== Component summary ==")
    for comp in sorted(component_stats):
        stats = component_stats[comp]
        print(f"  {comp}: {stats['pass']} pass / {stats['fail']} fail")
    print(f"EVAL {'PASSED' if not failed else 'FAILED'} "
          f"({passed} passed, {failed} failed)")
    return 0 if not failed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Tier-B nightly eval: golden cases through the real pipeline."
    )
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--only", default=None,
                        help="component prefix filter: own, doc, web, adv")
    parser.add_argument("--case", default=None, help="run one case id only")
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N selected cases")
    parser.add_argument("--list", action="store_true",
                        help="list case ids and exit (no keys needed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the golden file without LLM calls")
    args = parser.parse_args(argv)
    cases = load_golden(args.golden)
    if args.list:
        for case in cases:
            print(f"{case['id']}\t{case.get('query', '')}")
        return 0
    if args.only:
        cases = [c for c in cases if c["id"].startswith(args.only + "-")]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if args.limit:
        cases = cases[: args.limit]
    if args.dry_run:
        if not cases:
            print("No cases selected.")
            return 1
        return dry_run(cases)
    if not cases:
        print("No cases selected.")
        return 1
    if not ensure_live_keys():
        print("GROQ_API_KEY is required for live eval (real keys). "
              "Use --dry-run for keyless validation.")
        return 2
    return asyncio.run(live_main(cases, args.out_dir))


if __name__ == "__main__":
    raise SystemExit(main())
