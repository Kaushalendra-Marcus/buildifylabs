# Implementation Plan — LangGraph Orchestration (Incremental, Business-Logic-Preserving)

**Companion to `implementation-plan-continuity-and-completeness.md`.** This plan adopts LangGraph
for **orchestration only** — the sequencing/branching/retry/state-passing that `chat.py` and
`pipeline/run.py` currently hand-roll with `asyncio.gather`, nested `try/except`, and long parameter
lists. It deliberately does **not** touch any of the business logic those functions call
(`canonical.py`, `context_budget.py`, `guarantee.py`, `comparison/`, `stats.py`, `sql_generator.py`,
`query_rewriter.py`, `web_search.py`, `vector_store.py`) — every one of those keeps its exact current
signature and behavior, just gets called from a graph node instead of a plain function body.

**Read `implementation-plan-continuity-and-completeness.md` first if you haven't** — this plan
assumes Parts A–D from it are already live (in particular, `thread_id` now reaches every request
correctly, which this plan's checkpointer reuses directly).

---

## 0. Decision & scope

**Dependency: `langgraph` only, not the `langchain` package.** LangGraph is built by LangChain Inc
but is explicitly designed and documented to run standalone — `pip install langgraph` needs no
`langchain`/`langchain-core`/`langchain-<provider>` packages. This app keeps calling
`groq_service.py::generate_response`/`stream_response` exactly as today (raw Groq SDK, existing
JSON-schema-constrained decoding via `_to_strict_schema`) — LLM calls are plain Python inside node
functions, never wrapped in a LangChain chat-model class. This is a deliberate, narrow scope:
LangGraph's own docs describe it as the right tool for "a combination of deterministic and agentic
workflows, heavy customization, and carefully controlled latency" — which is exactly this pipeline's
shape — while adopting LangChain's LLM/prompt abstraction layer on top would cost precise control
over prompts (this codebase's prompts are hand-tuned down to exact wording — see the FORECAST RULE,
the citation-numbering scheme) for no offsetting benefit here.

**What becomes a graph:** the `/chat` request's full lifecycle — load context, resolve scope,
generate SQL, gather evidence (SQL execution + live search + document retrieval), compute
deterministic numbers, judge evidence sufficiency, narrate, validate/ground, synthesize visuals, log.
This is currently spread across `chat.py::_answer_request` (300+ lines) and
`pipeline/run.py::run_pipeline` (another long function calling nine-plus stages in sequence) — one
coherent flow, artificially split across two files today mostly because that's how it grew.

**What does not change:** every function those two currently call. `judge_sufficiency`,
`ensure_visuals`, `build_prompt`, `execute_sql`, `search_web`, `retrieve_document_evidence`,
`compute_forecast` — all keep their current signatures, current tests, current behavior. A node is a
thin wrapper: read what it needs from graph state, call the existing function unchanged, write the
result back into state.

**Genuine bonus, not the main goal:** LangGraph's checkpointer persists graph state keyed by a
`thread_id` — the exact identifier Part B of the companion plan just made real and stable. This
plan reuses that key directly (§4) rather than introducing a second thread concept.

## Non-negotiable constraints

1. **Zero intended behavior change.** This is a refactor of *how* steps are sequenced, not *what*
   they do. Every existing backend test must still pass without modification to its assertions.
2. **Business-logic functions are called, never re-implemented.** If a node's body is doing anything
   more than "read state → call an existing function → write state," stop — that logic belongs in
   the function being called, not the node.
3. **Ship behind a kill switch.** `ENABLE_LANGGRAPH_ORCHESTRATION` (config, default `False`),
   matching the exact shape of `ENABLE_LIVE_WEB_SCOPE`/`ENABLE_DOCUMENT_QA`. The hand-rolled path in
   `chat.py` stays intact and is what actually serves traffic until equivalence is proven (§5).
4. **Prove equivalence, don't assume it.** The centerpiece test strategy (§5) runs the same mocked
   scenario through both the old path and the new graph and asserts byte-identical output — a
   stronger bar than "the graph runs without crashing."
5. **Checkpointer reuses the existing Neon Postgres.** No new infrastructure — same principle as the
   pgvector decision in the companion plan.

---

## Phase 0 — Dependency + state schema

`Backend/requirements.txt`:
```
# Part E: graph-based orchestration for the /chat pipeline (business logic
# unchanged — see implementation-plan-langgraph-orchestration.md)
langgraph
langgraph-checkpoint-postgres
```

`Backend/app/config.py`:
```python
# --- LangGraph orchestration rollout (Part E) ---
ENABLE_LANGGRAPH_ORCHESTRATION: bool = Field(False, env="ENABLE_LANGGRAPH_ORCHESTRATION")
```

New file `Backend/app/services/llm/graph/state.py` — one `TypedDict` covering every value currently
threaded through `_answer_request`/`run_pipeline` as a parameter or local variable. This is
intentionally large and flat, mirroring what already exists as local variables today — do not
"clean it up" into nested sub-objects in this pass, that's a separate, optional follow-on once the
graph is live and stable:

```python
from typing import Any, Optional, TypedDict


class ChatGraphState(TypedDict, total=False):
    # Request + identity
    user_id: Any
    thread_id: str
    raw_query: str
    source_scope: str
    company_name: Optional[str]

    # Prior-turn context (Part A/B)
    prior_clarification: Optional[str]
    prior_data: Optional[dict]
    prior_research_state: Optional[dict]
    prior_query: Optional[str]
    plan_query: str
    sql_prior_context: Optional[str]
    effective_scope: str

    # SQL branch
    table_name: Optional[str]
    columns: list
    cleaned_sql: Optional[str]
    sql_error: Optional[str]
    sql_retry_count: int
    rows: list

    # Evidence branches
    news_context: list
    web_sources: list
    market_data: list
    fundamentals: list
    macro_data: list
    macro_note: str
    price_history: list
    financial_history: list
    research_notes: list
    document_context: list
    document_sources: list

    # Deterministic computation
    computed: dict

    # Research plan + judge
    plan_dict: dict
    decision: Any  # Decision (pipeline/models.py)

    # Terminal
    output: Any  # PipelineOutput — set by whichever node terminates the graph
```

## Phase 1 — Node wrappers (thin, zero logic change)

New file `Backend/app/services/llm/graph/nodes.py`. Every node is `async def node_x(state:
ChatGraphState) -> dict` (LangGraph merges the returned dict into state). Two representative examples
— the rest follow the identical pattern (full list at the end of this section):

```python
async def node_load_context(state: ChatGraphState) -> dict:
    """Wraps chat.py's existing _load_prior_context + _thread_id_for +
    clarification merge, verbatim — see chat.py for the exact logic this
    replaces (kept there too until Phase 5 cuts over)."""
    from app.routes.chat import _load_prior_context, _thread_id_for
    from app.services.data.comparison import merge_clarification_context

    thread_id = state["thread_id"]
    prior_clarification, prior_data, prior_research_state, prior_query = (
        await _load_prior_context(state["db"], state["user_id"], thread_id)
    )
    plan_query = state["raw_query"]
    if prior_clarification and prior_query:
        try:
            plan_query = merge_clarification_context(
                prior_query, prior_clarification, state["raw_query"]
            )
        except Exception:
            plan_query = state["raw_query"]
    sql_prior_context = prior_query if plan_query == state["raw_query"] else None
    return {
        "prior_clarification": prior_clarification,
        "prior_data": prior_data,
        "prior_research_state": prior_research_state,
        "prior_query": prior_query,
        "plan_query": plan_query,
        "sql_prior_context": sql_prior_context,
    }


async def node_gather_evidence(state: ChatGraphState) -> dict:
    """Wraps the EXISTING asyncio.gather(_execute_branch, _search_branch,
    _document_branch) verbatim. Deliberately NOT split into three separate
    LangGraph nodes with native fan-out/join in this phase — that's a real
    LangGraph feature (see "Not in this plan") but changes more surface
    area than this migration needs to prove out first. A node function can
    do its own internal concurrency; that's all this is."""
    import asyncio
    from app.routes.chat import _execute_branch_for_state, _search_branch_for_state, _document_branch_for_state

    exec_result, search_result, doc_result = await asyncio.gather(
        _execute_branch_for_state(state) if state.get("table_name") else asyncio.sleep(0, result=[]),
        _search_branch_for_state(state),
        _document_branch_for_state(state),
    )
    # ... unpack exactly as chat.py does today into news_context/web_sources/etc.
    # (elided here — identical to the existing unpacking block in chat.py,
    # moved verbatim, writing into the returned dict instead of locals)
    return {...}
```

The `_execute_branch`/`_search_branch`/`_document_branch` closures in `chat.py` today capture locals
via closure (`table_name`, `plan_query`, etc.) — they need converting to plain functions taking
`state` explicitly (`_execute_branch_for_state(state)`) since a graph node can't rely on Python
closures over `_answer_request`'s locals. This is the one real mechanical change Phase 1 requires;
the logic inside each is copied verbatim, only the parameter-passing style changes.

**Full node list** (same pattern as the two above — thin wrapper, existing function, unchanged logic):

| Node | Wraps |
|---|---|
| `node_load_context` | `_load_prior_context`, `_thread_id_for`, `merge_clarification_context` |
| `node_resolve_scope` | effective_scope logic incl. "Yes, check live sources too", `ENABLE_LIVE_WEB_SCOPE` kill switch, `wants_external_context` clarification |
| `node_check_has_data` | `_user_has_data` |
| `node_generate_sql` | `get_table_columns`, `build_sql_prompt`, `generate_response`, `clean_sql_response` |
| `node_gather_evidence` | the existing `asyncio.gather` of SQL execution + `search_web` + `retrieve_document_evidence` |
| `node_sql_repair` | the existing 422-repair retry logic in `_execute_branch`, made an explicit bounded loop (§3) |
| `node_compute_deterministic` | `compute_statistics`, `parse_what_if`/`apply_what_if`, `is_forecast_query`/`infer_forecast_columns`/`compute_forecast` |
| `node_budget_merge` | `fit_pairs_to_budget` document/web merge (context_budget.py) |
| `node_build_plan` | `build_research_plan` |
| `node_judge` | `judge_sufficiency` |
| `node_narrate` | `build_prompt`, `generate_response` (narration call) |
| `node_ground_and_validate` | `normalize_pipeline_payload`, `sanitize_citations`, `apply_narration_contract`, `build_validated_evidence_state`, `drop_ungrounded_visuals_evidence`, `final_response_validation` |
| `node_synthesize_visuals` | `plan_visuals_from_evidence`, `ensure_visuals` |
| `node_log_and_return` | `_log_and_return` |

## Phase 2 — Graph wiring

New file `Backend/app/services/llm/graph/build.py`:

```python
from langgraph.graph import StateGraph, END
from .state import ChatGraphState
from . import nodes

def build_chat_graph():
    graph = StateGraph(ChatGraphState)

    graph.add_node("load_context", nodes.node_load_context)
    graph.add_node("resolve_scope", nodes.node_resolve_scope)
    graph.add_node("check_has_data", nodes.node_check_has_data)
    graph.add_node("generate_sql", nodes.node_generate_sql)
    graph.add_node("gather_evidence", nodes.node_gather_evidence)
    graph.add_node("sql_repair", nodes.node_sql_repair)
    graph.add_node("compute_deterministic", nodes.node_compute_deterministic)
    graph.add_node("budget_merge", nodes.node_budget_merge)
    graph.add_node("build_plan", nodes.node_build_plan)
    graph.add_node("judge", nodes.node_judge)
    graph.add_node("narrate", nodes.node_narrate)
    graph.add_node("ground_and_validate", nodes.node_ground_and_validate)
    graph.add_node("synthesize_visuals", nodes.node_synthesize_visuals)
    graph.add_node("log_and_return", nodes.node_log_and_return)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "resolve_scope")

    # Early exits stay early exits: a clarification request or a "no data
    # yet" fallback ends the graph here, exactly like chat.py's early
    # `return await _log_and_return(...)` calls today.
    graph.add_conditional_edges(
        "resolve_scope",
        lambda s: "needs_clarification" if s.get("early_output") else "check_has_data",
        {"needs_clarification": "log_and_return", "check_has_data": "check_has_data"},
    )
    graph.add_conditional_edges(
        "check_has_data",
        lambda s: "no_data" if s.get("early_output") else "generate_sql",
        {"no_data": "log_and_return", "generate_sql": "generate_sql"},
    )
    graph.add_edge("generate_sql", "gather_evidence")
    graph.add_conditional_edges(
        "gather_evidence",
        lambda s: "repair" if s.get("sql_error") and s.get("sql_retry_count", 0) == 0 else "compute",
        {"repair": "sql_repair", "compute": "compute_deterministic"},
    )
    graph.add_edge("sql_repair", "compute_deterministic")
    graph.add_edge("compute_deterministic", "budget_merge")
    graph.add_edge("budget_merge", "build_plan")
    graph.add_edge("build_plan", "judge")
    graph.add_conditional_edges(
        "judge",
        lambda s: "clarify" if s["decision"].must_clarify else "narrate",
        {"clarify": "log_and_return", "narrate": "narrate"},
    )
    graph.add_edge("narrate", "ground_and_validate")
    graph.add_edge("ground_and_validate", "synthesize_visuals")
    graph.add_edge("synthesize_visuals", "log_and_return")
    graph.add_edge("log_and_return", END)

    return graph
```

This is a **readable, literal picture of the exact flow already described in `run.py`'s own comments**
today ("The canonical ResearchPlan is the single source of truth...", "Prior research reuse is
explicit + validated..."). The value here isn't new behavior — it's that this flow is now visible as
a graph instead of buried in 300 lines of sequential prose-commented Python, and each conditional
exit point (clarify, no-data, sql-repair) is an explicit edge instead of a `return
await _log_and_return(...)` sprinkled mid-function.

## Phase 3 — SQL retry as a real bounded loop (not a hand-rolled one-shot)

Today's 422-repair is a single hard-coded retry nested inside a `try/except HTTPException` block in
`_execute_branch`. Phase 2's `sql_repair` edge makes this an explicit, inspectable loop with a
counter already in `ChatGraphState` (`sql_retry_count`). `node_sql_repair` increments the counter and
loops back to `gather_evidence` (not forward to `compute_deterministic`) so a repaired query still
goes through the exact same execution path as a first-try success:

```python
graph.add_edge("sql_repair", "gather_evidence")  # replaces the Phase-2 edge above
graph.add_conditional_edges(
    "gather_evidence",
    lambda s: (
        "repair" if s.get("sql_error") and s.get("sql_retry_count", 0) < 1
        else "compute"
    ),
    {"repair": "sql_repair", "compute": "compute_deterministic"},
)
```

Bounded at 1 retry — identical to today's behavior. The number is now a single constant to change
later if warranted, instead of restructured `try/except` nesting.

## Phase 4 — Checkpointing (reuses Part B's `thread_id`)

New file `Backend/app/services/llm/graph/checkpointer.py`:

```python
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver


@lru_cache()
def get_checkpointer():
    """Postgres-backed in prod (same Neon DB — no new infra), in-memory for
    tests/local dev without a DATABASE_URL pointed at a real Postgres —
    same fallback shape as web_search_cache.py's Redis-or-memory pattern."""
    from app.config import get_settings

    settings = get_settings()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(settings.DATABASE_URL)
    except Exception:
        return MemorySaver()
```

Invocation in `chat.py`:
```python
result_state = await compiled_graph.ainvoke(
    initial_state, config={"configurable": {"thread_id": thread_id}}
)
output = result_state["output"]
```

**Explicitly not in scope for this phase:** replacing `_load_prior_context`'s `QueryLogs`-based
lookup with the checkpointer's own state recall. `QueryLogs` also backs `POST /chat/flag` and any
future admin/analytics surface — collapsing those two persistence mechanisms into one is a real,
separate design decision (which state is authoritative when they'd disagree?) and does not belong in
a migration whose entire premise is "zero behavior change." Revisit once the graph has been running
in production for a while — see "Not in this plan."

## Phase 5 — Safe rollout: flag + equivalence tests

`chat.py::_answer_request` gains a single branch at the top:

```python
if get_settings().ENABLE_LANGGRAPH_ORCHESTRATION:
    from app.services.llm.graph.build import build_chat_graph
    from app.services.llm.graph.checkpointer import get_checkpointer
    compiled = build_chat_graph().compile(checkpointer=get_checkpointer())
    result_state = await compiled.ainvoke(
        _initial_state(request, user_id, db),
        config={"configurable": {"thread_id": _thread_id_for(request)}},
    )
    return result_state["output"]
# ... existing hand-rolled implementation, unchanged, below this line
```

**Equivalence test** — `Backend/tests/test_graph_equivalence.py`, the centerpiece of this plan's test
strategy: for each of the existing `test_chat_api.py` scenarios (happy-path SQL, live-web, both,
clarification, document-only, forecast, what-if, SQL-repair), run the identical mocked request
through both code paths (flag off, flag on) and assert the two `PipelineOutput`s are equal field by
field. This is strictly stronger than "the graph doesn't crash" — it proves the refactor changed
nothing observable. Only once every scenario passes equivalence does `ENABLE_LANGGRAPH_ORCHESTRATION`
default flip to `True`.

**Graph-structure tests** — `Backend/tests/test_chat_graph.py`: the compiled graph has exactly the
nodes/edges listed in Phase 2 (catches an accidental miswiring that equivalence tests might not
exercise if two paths happen to coincide); the SQL-repair loop actually terminates after 1 retry on a
synthetic always-422 SQL branch (proves the bounded loop is truly bounded, not just bounded in the
happy case).

## Phase 6 — Cutover + cleanup (follow-on, not part of this plan)

Once `ENABLE_LANGGRAPH_ORCHESTRATION=True` has run in production without incident: delete the
hand-rolled implementation from `chat.py` and `run_pipeline`'s internals, leaving only the graph path
and the node wrappers (which can then absorb their wrapped function's logic directly, removing a
layer of indirection). This is a deliberate, separate, lower-risk cleanup pass — do not do it in the
same change that introduces the graph.

---

## Not in this part (explicitly out of scope)

- **True multi-node fan-out/join for evidence gathering.** LangGraph supports native parallel branches
  (`add_edge` from one node to several, joined by a downstream node LangGraph waits on) — Phase 1
  deliberately keeps the existing `asyncio.gather` inside one node instead. Splitting SQL
  execution/web search/document retrieval into three真 graph-parallel nodes is a legitimate future
  refinement once the team is comfortable with the graph, not a Phase-1 requirement.
- **Human-in-the-loop interrupts** (LangGraph's `interrupt()` primitive) — this product has no
  "pause for a human reviewer" step today; nothing to attach it to yet.
- **Collapsing `QueryLogs` and the checkpointer into one persistence mechanism** — flagged explicitly
  in Phase 4 as a real, separate decision.
- **A genuine agentic tool-use loop** (LLM decides mid-request whether to search again, call another
  tool, etc.) — today's `judge` step runs exactly once per request; it is not a loop. Building a real
  ReAct-style loop on top of this graph is the natural next step *if and when* the product needs an
  LLM that can decide to gather more evidence based on what it already found — this plan lays the
  graph foundation for that without building it prematurely.
- **Streaming through the graph.** `POST /chat/stream` exists today via a separate code path
  (`stream_response`); wiring LangGraph's own streaming primitives through it is a follow-on, not
  needed for this migration's stated goal.

## Definition of done

- [ ] `ENABLE_LANGGRAPH_ORCHESTRATION=false` (default): behavior is byte-identical to today — the
      flag exists but changes nothing until flipped.
- [ ] Every node in Phase 1's table wraps an existing function with zero logic changes to that
      function.
- [ ] The SQL-repair retry is bounded at 1, matching current behavior, now as an explicit graph edge.
- [ ] Checkpointing works against the existing Neon Postgres — no new infrastructure.
- [ ] `test_graph_equivalence.py` passes for every scenario in `test_chat_api.py` with the flag both
      off and on.
- [ ] `test_chat_graph.py` verifies the compiled graph's structure and the bounded retry loop.
- [ ] Full existing test suite stays green throughout — no test's assertions change, only new tests
      are added.
