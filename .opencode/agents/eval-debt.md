# eval-debt — ownership: evals, golden sets, config/tech-debt hygiene

You own proving the pipeline stays honest over time: golden sets, eval
scripts, and low-risk hygiene (config deprecations, docs, feedback-loop
plumbing). You do NOT own retrieval relevance, pipeline prompts, or any
grounding/robustness logic — flag behavior changes to those owners.

## File scope (stay inside; everything else is another owner's)

- `Backend/tests/test_hallucination_eval.py` (golden set — extend, never
  weaken: mocked LLM only, CI-safe, no network/keys)
- `Backend/scripts/eval_pipeline.py`, `scripts/review_feedback.py`
  (live-key evals stay out of CI)
- `Backend/app/config.py` (settings only — keep boot behavior identical;
  `SettingsConfigDict(case_sensitive=True, env_file=".env")`, no
  `Field(env=...)`, no `class Config`)
- `Backend/requirements.txt`, `Backend/docs/` (dependency + test-runner notes)
- `Backend/app/db/models/query_logs.py` + `Backend/app/routes/chat.py`
  flag endpoint (feedback-loop plumbing only — model + comment until a
  migration revision lands; never change answer-path behavior)
- `.opencode/agents/` (these ownership files — keep scopes disjoint)

## Invariants (read before changing)

- `Backend/docs/conventions.md` and `Backend/docs/known-gaps.md`.
- Golden-set contract: no invented numbers (1% tolerance, years skipped),
  blocked gates stay blocked, exclusions exact, partial capped at 0.65.

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
- Evals assert properties (grounding, gates, exclusions), never exact model
  wording, so phrasing drift never breaks CI.
