"""CI coverage for the eval tooling itself.

The nightly evals need live keys, so they never run in CI - which means a bug in
their *checkers* would go unnoticed until a real run. These tests exercise the
property checks, the SQL golden fixtures (offline), and the guard report's
aggregation with no network.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- eval_pipeline property checks ---------------------------------------------------

def _case(expect, rows=None, query="revenue by region"):
    return {
        "id": "t-01",
        "query": query,
        "source_scope": "own_data",
        "seed": {"rows": rows if rows is not None else [{"region": "East", "revenue": 500}]},
        "expect": expect,
    }


def _output(answer, confidence=0.7, clarification=None):
    return SimpleNamespace(
        answer=answer, visuals=[], clarification=clarification,
        confidence=confidence, research_state={},
    )


def test_eval_flags_an_invented_number():
    mod = _load("eval_pipeline")
    case = _case({"no_invented_numbers": True})
    computed = mod.precompute_numbers(case["query"], case["seed"]["rows"])
    failures = mod.check_case(case, _output("East revenue was 999."), computed)
    assert any("untraceable" in failure for failure in failures)


def test_eval_accepts_a_grounded_number():
    mod = _load("eval_pipeline")
    case = _case({"no_invented_numbers": True})
    computed = mod.precompute_numbers(case["query"], case["seed"]["rows"])
    assert mod.check_case(case, _output("East revenue was 500."), computed) == []


def test_eval_confidence_and_phrase_checks():
    mod = _load("eval_pipeline")
    case = _case({"max_confidence": 0.5, "must_contain": ["east"], "must_not_contain": ["guarantee"]})
    failures = mod.check_case(case, _output("East will guarantee growth.", confidence=0.9), {})
    joined = " ".join(failures)
    assert "max_confidence" in joined
    assert "must_not_contain" in joined
    assert "must_contain" not in joined.replace("must_not_contain", "")


def test_eval_loader_rejects_unknown_expect_keys(tmp_path):
    mod = _load("eval_pipeline")
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps({
        "id": "x-01", "query": "q", "source_scope": "own_data", "seed": {},
        "expect": {"definitely_not_a_real_check": True},
    }) + "\n")
    try:
        mod.load_golden(path)
    except SystemExit as exc:
        assert "unknown expect keys" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("loader accepted an unknown expect key")


def test_eval_llm_hook_keys_are_stable_and_prompt_sensitive():
    mod = _load("eval_pipeline")
    base = {"model": "m", "system_prompt": "s", "prompt": "p"}
    assert mod._llm_key(base) == mod._llm_key(dict(base))
    assert mod._llm_key(base) != mod._llm_key({**base, "prompt": "p2"})


# --- SQL golden fixtures (offline) ---------------------------------------------------

def test_sql_golden_fixtures_reproduce_their_expected_rows():
    mod = _load("eval_sql")
    fixtures, cases = mod.load_cases(mod.DEFAULT_GOLDEN)
    assert len(cases) >= 8
    assert any(case.get("expect_invalid") for case in cases)
    assert asyncio.run(mod.dry_run_all(fixtures, cases)) == []


def test_sql_row_matching_ignores_column_order_aliases_and_row_order():
    mod = _load("eval_sql")
    expected = {"rows": [["North", 550], ["East", 500]], "mode": "exact", "ordered": False}
    actual = [{"total": 500, "region": "East"}, {"total": 550.2, "region": "North"}]
    assert mod.rows_match(actual, expected)
    assert not mod.rows_match(actual[:1], expected)
    assert not mod.rows_match([{"region": "East", "total": 400}, actual[1]], expected)


def test_sql_row_matching_subset_mode_and_ordering():
    mod = _load("eval_sql")
    assert mod.rows_match([{"region": "North", "total": 550}], {"rows": [["North"]], "mode": "subset"})
    ordered = {"rows": [["a", 1], ["b", 2]], "ordered": True}
    assert mod.rows_match([{"k": "a", "v": 1}, {"k": "b", "v": 2}], ordered)
    assert not mod.rows_match([{"k": "b", "v": 2}, {"k": "a", "v": 1}], ordered)


# --- guard report -----------------------------------------------------------------------

def test_guard_report_aggregates_fire_rates_and_calibration():
    mod = _load("guard_report")
    fired_high = json.dumps({
        "confidence": 0.9,
        "research_state": {"guards": ["prose_number_untraceable: 1 number(s): 7,777"]},
    })
    quiet_high = json.dumps({"confidence": 0.9, "research_state": {}})
    fired_low = json.dumps({
        "confidence": 0.2, "research_state": {"guards": ["citation_unsupported"]},
    })
    summary = mod.summarize([(fired_high, True), (quiet_high, False), (fired_low, True), ("not json", False)])
    assert summary["total"] == 3
    assert summary["flagged"] == 2
    assert summary["answers_with_guard"] == 2
    assert summary["flagged_with_guard"] == 2
    assert summary["fired"]["prose_number_untraceable"] == 1
    low_label, high_label = mod._BUCKETS[0][2], mod._BUCKETS[2][2]
    assert summary["buckets"][high_label] == [2, 1]
    assert summary["buckets"][low_label] == [1, 1]
    assert "prose_number_untraceable" in mod.render(summary, 7)
