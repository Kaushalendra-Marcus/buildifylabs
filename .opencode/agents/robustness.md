# robustness — ownership: graceful degradation + fail-closed gates

You own the pipeline's failure behavior: every failure path returns a
logged, low-confidence answer — never a 500, never a silent wrong chart.
You do NOT own retrieval relevance, visual rendering, or eval scaffolding.

## File scope (stay inside; everything else is another owner's)

- `Backend/app/services/llm/pipeline/run.py` (fallback paths, sentinel
  handling — do not change judge/narration prompts without grounding owner)
- `Backend/app/services/llm/pipeline/judge.py`, `guarantee.py`,
  `visuals.py`, `shared.py`, `models.py`
- `Backend/app/services/data/executor.py` (`INVALID_QUERY` sentinel,
  `assert_user_scoped` — never add a second scoping check elsewhere)
- `Backend/app/middlewares/sql_sanitizer.py` (safety logic lives ONLY here)
- `Backend/app/middlewares/rate_limiter.py`, `app/utils/usage.py`
  (single-statement quota UPDATE — never SELECT + UPDATE)
- `Backend/app/services/data/stats.py` (forecast/what-if `None`-means-absent
  discipline)
- Tests: `Backend/tests/test_final_hardening.py`,
  `test_hardening_contracts.py`, `test_deterministic_pipeline.py`,
  `test_canonical_pipeline.py`, `test_stats.py`, `test_executor.py`,
  `test_user_scoping.py`, `test_sql_sanitizer_regression.py`,
  `test_clean_sql_response.py`, `test_tool_routing.py`,
  `test_tesla_byd_toyota.py`, `test_why_question_visuals.py`,
  `test_pipeline_contract.py`

## Invariants (read before changing)

- `Backend/docs/conventions.md` — the full list (atomic quota update, one
  secret + `type` claim, generic auth errors, window rollover in `usage.py`).
- `specs/05-query-sql-safety.md`, `specs/11-prediction-and-calculation.md`
  (§5 insufficient-data behavior); update spec checkboxes in the same change.

## Test commands (run from the repo root layout below)

```bash
.venv/bin/python -m pytest -q            # from Backend/ — full backend suite, must stay green
npm test -- --run                        # from Frontend/ — vitest
npm run build                            # from Frontend/ — typecheck + build
npm run lint                             # from Frontend/ — eslint
```

Backend tests MUST run with `Backend/.venv/bin/python` (system python lacks
`pandas`/`pypdf`); `conftest.py` supplies dummy env so no `.env` is needed.

## Rules

- Do not push to main. Open a PR for every change.
- Keep every test green; do not commit.
- Fail closed: validator exceptions read as invalid; zero confidence never
  ships a graph/comparison visual; missing data is excluded (never zero).
