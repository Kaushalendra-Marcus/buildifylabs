# grounding-retrieval — ownership: grounded retrieval + evidence

You own the pipeline's evidence path: what enters the prompt must be
traceable to a retrieved row, snippet, or computed number. You do NOT own
visual rendering, quota/auth, or eval scaffolding.

## File scope (stay inside; everything else is another owner's)

- `Backend/app/services/data/comparison/` (entities, evidence, figures,
  plans, currency, timeframe)
- `Backend/app/services/data/vector_store.py` (PDF keyword retrieval)
- `Backend/app/services/data/pdf_parser.py`, `parser.py`, `storage.py`
- `Backend/app/services/llm/pipeline/grounding.py`, `prompting.py`,
  `prompts.py`, `figures.py`, `deps.py`, `history.py`
- `Backend/app/services/web_search.py`, `web_search_cache.py`
- `Backend/app/services/llm/context_budget.py`
- `Backend/app/routes/chat.py` (evidence branches only — do not touch the
  flag endpoint or rate-limiter wiring)
- Tests: `Backend/tests/test_*comparison*.py`, `test_evidence_*.py`,
  `test_pdf_parser.py`, `test_context_budget.py`, `test_vector_store.py`,
  `test_ghost_entities_regression.py`, `test_query_rewriter.py`

## Invariants (read before changing)

- `Backend/docs/conventions.md` — SQL safety lives only in
  `sql_sanitizer.py`; tenant scoping only in `executor.py`.
- `specs/06-ai-insight-pipeline.md` + `specs/10-trust-safety-compliance.md`
  for grounding contracts; update the spec's checkboxes in the same change.

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
- Never invent numbers in answers or visuals — deterministic computation in
  code, narration only.
