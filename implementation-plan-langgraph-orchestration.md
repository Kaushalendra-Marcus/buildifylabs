# LangGraph Orchestration Plan (Repository-Verified)

Status: design only. LangGraph is **not installed, enabled, or required by the
current application**. This document must not be treated as an implementation.

## Verified current flow

The current request path is:

1. [`app/routes/chat.py::_answer_request`](./Backend/app/routes/chat.py) loads
   the request-scoped `AsyncSession`, user identity, thread context, and quota
   state.
2. The route generates SQL, then runs its local evidence closures. SQL and
   live-web work may overlap, but database access remains tied to the
   request-owned session.
3. Deterministic calculations are performed in the data services.
4. [`app/services/llm/pipeline/run.py::run_pipeline`](./Backend/app/services/llm/pipeline/run.py)
   performs judge, narration, grounding, prose guards, and visual guarantees.
5. The route logs the final `PipelineOutput` and supports both unary and SSE
   responses. SSE cancellation must cancel the background task and prevent
   post-disconnect paid work.

The current implementation contains a local `_execute_branch` closure in
`_answer_request`; symbols named `_execute_branch_for_state`,
`_search_branch_for_state`, and `_document_branch_for_state` do not currently
exist. They must not be referenced as existing helpers.

## Non-negotiable production constraints

- Keep the feature disabled until equivalence tests pass.
- Do not move business logic into graph nodes.
- Preserve `AsyncSession` ownership. A graph node must not concurrently use the
  same SQLAlchemy session from multiple tasks.
- Preserve `on_stage`, `on_token`, cancellation, fallback, logging, quota, and
  `cost_ledger` behavior.
- Do not use an in-memory checkpointer in production or silently fall back to
  one when Postgres is unavailable.
- Do not persist secrets, raw uploaded content, or unbounded model output in
  checkpoints.
- Validate the `thread_id` length and use a stable, user-scoped identifier.

## Phase 0: dependency and compatibility proof

Before editing application code:

1. Select and pin compatible versions of `langgraph` and the Postgres
   checkpointer package in `Backend/requirements.txt`.
2. Verify the installed Python version, LangGraph API, async checkpointer
   lifecycle, and existing SQLAlchemy/asyncpg versions.
3. Add a startup configuration check requiring a persistent checkpointer when
   the feature flag is enabled.
4. Add a migration/retention policy for checkpoint rows.

No application import should reference LangGraph while the flag is disabled if
the dependency is optional for local development.

## Phase 1: extract pure, tested boundaries

Do not create a large `TypedDict` from guessed locals. First introduce small
typed request/context objects from values that are actually passed today:

- request identity: `user_id`, `thread_id`, raw query, requested scope;
- prior-turn context returned by `_load_prior_context`;
- SQL/evidence results returned by the existing closures;
- deterministic computation result;
- the existing `Decision` and `PipelineOutput` types.

Each extraction must have characterization tests against the current
`_answer_request` path. The extracted functions must retain the existing
signatures of business-logic services.

## Phase 2: graph behind a kill switch

Add a setting such as `ENABLE_LANGGRAPH_ORCHESTRATION=False`. The default path
must remain the current route implementation.

The first graph should contain only one complete vertical slice:

```text
load_prior_context
  -> existing SQL/evidence orchestration
  -> existing run_pipeline
  -> existing logging/return
```

The graph node may call the existing orchestration function. It must not
reimplement its branches from pseudocode. Native fan-out should not be used
until separate-session requirements and callback semantics are proven.

Compile the graph once per process/configuration, not once per request. Pass
`thread_id` through the graph configuration only after validating it. Keep
request-scoped callbacks and database sessions outside persistent state.

## Phase 3: persistence

Use the documented async Postgres checkpointer only after:

- the checkpointer tables are created explicitly during deployment;
- startup fails clearly if Postgres persistence is unavailable;
- checkpoint retention is configured;
- sensitive fields are excluded or redacted;
- concurrent requests for one thread have a defined policy.

`MemorySaver` is allowed only in isolated tests. It is not a production
fallback.

## Phase 4: equivalence and rollout

For each existing scenario in `Backend/tests/test_chat_api.py` and the pipeline
tests, compare the flag-off and flag-on paths for:

- final `PipelineOutput` fields;
- SQL and row-slice traceability;
- clarification and no-data early exits;
- live-web/document evidence;
- forecast and what-if calculations;
- validation/grounding/prose-guard behavior;
- SSE stage and token behavior;
- QueryLog side effects;
- cancellation and exception fallbacks.

Only after these tests pass should the flag be enabled in a canary environment.
Keep the old path available for rollback. Remove it only in a separate change
after production observation.

## Explicitly not claimed

This plan does not claim that LangGraph is required for the current product,
that the migration is behavior-preserving, that a Postgres checkpointer is
configured, or that any node/helper listed above already exists.

The authoritative persistence guidance is:

- <https://docs.langchain.com/oss/python/langgraph/persistence>
- <https://docs.langchain.com/oss/python/langgraph/checkpoints>

The current AI reliability controls remain the deterministic guards in
`Backend/app/services/llm/pipeline/`; LangGraph alone does not prevent
hallucinations.
