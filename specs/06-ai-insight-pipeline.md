# Spec 06 — AI Insight & Visual Pipeline

**Status:** ✅ Implemented (B4) — LLM calling layer + structured-output pipeline exist **and are
reachable** via `POST /chat` (`app/routes/chat.py`). `langchain_pipeline.py` now matches the §3
contract below (7 real `visual_type`s, `props`, bounded `confidence`, `clarification`), `run_pipeline`
consumes deterministic stats computed in pandas (spec `11` §3.1), and large `db_data` is truncated
before prompting (edge case 6).
**Source files:** `app/services/llm/groq_service.py`, `app/services/llm/langchain_pipeline.py`,
`app/services/data/stats.py`, `app/routes/chat.py`

---

## 1. Problem Statement

The product's differentiator beyond raw charts is explaining **why** something happened in the
user's data — and, per `11-prediction-and-calculation.md`, what's likely to happen next — optionally
correlating it with real-world news, and returning everything in one strict, machine-readable shape
the frontend can render without further parsing or guessing.

## 2. Functional Requirements

- **FR1:** Given a user query + business data + optional news context, return one structured JSON
  object matching `PipelineOutput`.
- **FR2:** Output must include: `answer`, `visuals[]` (each with `visual_type`, `props`, `title`),
  `insights[]`, `summary`, `root_causes[]`, `recommendations[]`, `news_context[]`, `anomalies[]`,
  `confidence`, and `clarification` (optional — see FR7).
- **FR3:** `visual_type` must be one of the **7 types that actually exist as frontend components**
  (`13-frontend-migration.md`): `metric`, `graph`, `table`, `comparison`, `insight`, `alert`,
  `status`. Each has its own prop shape — see §3. This replaces the earlier 9-type list
  (`line_chart`/`bar_chart`/etc.), which never matched anything actually built.
- **FR4:** If the LLM's JSON is malformed or fails schema validation, degrade gracefully to a safe
  fallback `PipelineOutput` (empty lists, `confidence = 0.0`, a generic message) rather than raising
  to the caller.
- **FR5:** Provider selection (which LLM answers a given call) is delegated entirely to
  `12-llm-orchestration.md` — this spec defines *what* gets asked and in *what shape the answer must
  come back*, not which provider answers it.
- **FR6:** External (live-web) context is included in the prompt only per the user-directed
  `source_scope` (`"own_data" | "live_web" | "both"`, spec `07`) — never inferred from query
  content alone. This replaces the earlier opt-in-boolean design; `07`'s FR4 defines how a
  free-text request that disagrees with the current selector is resolved (via FR7's clarification
  mechanism, not silently).
- **FR7 (new):** The pipeline can return a **clarifying question instead of a final answer**, per
  the ask-don't-guess pattern in `11-prediction-and-calculation.md` §4. When it does,
  `PipelineOutput.clarification` is populated (`{ question: str, options: list[str] }`) and the
  other answer fields are empty — the frontend renders this as a quick-pick prompt, and the user's
  choice becomes the next turn's input, not a fresh unrelated question.
- **FR8 (new): Decision-driven loop.** Before narrating, an LLM sufficiency judge decides
  `answer` vs `clarify` from the actual tool outputs on hand (row/column inventory, computed-stat
  keys, web snippet and market-series counts) plus prior-turn context, and plans visuals by data
  shape. Clarification is a last resort: never a second question on an already-asked point (the
  route feeds the prior clarification in; a repeat is re-narrated once with clarification disabled
  and answered best-effort with stated assumptions), and options are grounded in real evidence.
  Empty options are backfilled from the judge's suggestions so pills and the free-text box render
  together; genuinely open-ended questions keep `[]` (type-only).
- **FR9 (new): Visual guarantee.** A validated normal answer never goes out naked while plottable
  tool outputs exist: deterministic code synthesizes a chart (date→line, few categories→bar),
  a grounding table, and a headline metric from real rows/stats/series only — never invented
  values. Follow-ups referencing the previous answer ("chart that") resolve from its logged rows.
  Qualitative web-only answers carry at least one snippet-grounded insight card instead.
- **FR10 (new): Framed web retrieval.** Live scopes reframe the chat message into 1–3 clean
  search queries via LLM (merging appended clarification answers into intent; one variant may
  target community discussion via a site: restriction when opinions/experiences are sought),
  fan out across them with dedupe, and resolve any named entity to a market symbol generically
  (alias fast path, then symbol search). Raw user text is never sent to search as-is. Snippets
  and sources stay aligned 1:1 (title+snippet merged with its page URL; provider-only entries
  kept, never dropped), so every citation `[n]` resolves to a listed source.
- **FR11 (new): Citations, thinking, requested shapes.** Web snippets are numbered and factual
  claims carry `[n]` markers (phantom markers are stripped in code); every run records a
  machine-written thinking trace (`thinking: list[str]`, additive/optional for clients); an
  explicitly requested output shape (bar/line/pie/area/table/metric) is honored end-to-end from
  query text through synthesis. Empty model completions retry once before degrading.
- **FR12 (new): Structured calls use JSON mode; answers carry follow-ups.** Judge, narration, and
  query-framing calls set Groq's `response_format: json_object` (the HF fallback has no such flag
  and is unaffected, so parsing stays defensive). Normal answers include 2–3 tap-to-ask
  `followups` (capped/cleaned in code; `[]` when none fit). Model copy may use inline markdown
  (`**bold**`, `*italic*`, `` `code` ``) — the frontend renders it; citations still parse first.

## 3. API Contracts (internal — no HTTP surface yet)

**`generate_response(...)`** — superseded by `generate_completion(...)` in
`12-llm-orchestration.md` §4; update call sites accordingly when that spec is implemented. Until
then, the existing Groq→HF-only behavior stands as an interim implementation.

**`run_pipeline(user_query: str, db_data: list[dict], computed_numbers: dict | None = None, news_context: list | None = None, source_scope: Literal["own_data", "live_web", "both"] = "own_data", company_name: str | None = None) -> PipelineOutput`**
✅ Live (B4), extended since: optional `prior_clarification`, `prior_data` (previous answer's row
digest from QueryLogs), `market_data`, and `web_sources` params feed the FR8 decision loop and the
FR9 guarantee; `thinking: list[str]` joined the output (optional, additive). The old
`include_news: bool` param became `source_scope`, the mutable-default
`news_context: list = []` was fixed to `None`, `visual_type`/`confidence`/`clarification` match FR3
below, and `db_data` is truncated before prompting. `db_data` and `news_context` are both fetched
by the caller according to `source_scope` (per specs `05` and `07`) — this function does not decide
what to fetch, only how to narrate what it's given. `computed_numbers` (spec `11` §3.1 stats) is
supplied the same way — as data the LLM narrates, never something it's asked to calculate.

```python
class VisualOutput(BaseModel):
    visual_type: Literal["metric", "graph", "table", "comparison", "insight", "alert", "status"]
    props: Dict           # shape depends on visual_type — see src/lib/schemas/visuals.ts
                           # in the frontend for the authoritative per-type prop schema
    title: str

class ClarificationRequest(BaseModel):
    question: str
    options: List[str]  # default []; a model-emitted null coerces to [] (validator)

class Decision(BaseModel):  # FR8 sufficiency judge verdict (internal, never served)
    decision: Literal["answer", "clarify"]
    missing: str = ""
    chart_from_prior: bool = False
    visual_plan: List[VisualPlanItem] = []
    suggested_options: List[str] = []

class PipelineOutput(BaseModel):
    answer: str
    visuals: List[VisualOutput]
    insights: List[str]
    summary: str
    root_causes: List[str]
    recommendations: List[str]
    news_context: List[str]
    anomalies: List[str]
    confidence: float = Field(ge=0.0, le=1.0)      # now bounded — was an open gap, closed here
    clarification: Optional[ClarificationRequest] = None
```

The per-`visual_type` `props` shape is intentionally not duplicated here — `visuals.ts` in the
frontend is the single source of truth for what each of the 7 types expects, so the two can't drift
independently. If a `props` shape needs to change, change it there first, then update this spec's
reference to it.

## 4. Constraints

- `max_tokens = 2000` for the structured pipeline call — unchanged from before, still worth
  monitoring fallback rate in logs once live, more so now that FR7's `clarification` field adds
  another shape the JSON needs to reliably produce.
- Temperature stays fixed low (`0.2`) for the structured pipeline call, favoring JSON reliability.
- This module does not fetch its own data — `db_data` (spec `05`) and `news_context` (spec `07`)
  must be supplied by the caller. Deterministically-computed numbers from `11` (forecasts, what-ifs,
  stats) are supplied the same way — as data the LLM narrates, never as something it's asked to
  calculate itself.

## 5. Edge Cases & Error Handling

1. **Valid JSON but missing a required field** → `ValidationError` → fallback response
   (`confidence = 0.0`), logged.
2. **Non-JSON prose returned** → `extract_json` raises `ValueError` → fallback response, logged.
3. **`visual_type` outside the 7 allowed values** — now a genuine schema validation failure
   (`Literal[...]`, not a plain `str`), not a silent pass-through. Falls into edge case #1's
   handling.
4. **`confidence` outside 0.0–1.0** — now a genuine schema validation failure (`Field(ge=0.0,
   le=1.0)`), same as above. Both of these were open gaps in the original version of this spec;
   they're closed by the schema shown in §3, not by any additional runtime check.
5. **Both/all configured providers fail** — per `12-llm-orchestration.md`, this is now a multi-
   provider cascade, not a single fallback — the "both failed" case becomes "the whole cascade
   failed," which should get its own dedicated log line distinct from a single-provider failure, to
   aid debugging in production.
6. **Very large `db_data`** (thousands of rows) — no truncation/summarization step exists before
   injecting it into the prompt. Needs a row-count/size cap or a summarize-before-prompt step before
   production use.
7. **A `clarification` response where the user's next message doesn't clearly answer it** — the
   pipeline should still attempt to proceed rather than loop indefinitely; treat an unclear
   follow-up as a fresh query rather than re-asking the same clarification a second time.
8. **[Gap] `langchain_pipeline.py` has not actually been updated to this file's §3 contract.** The
   source still has `visual_type: str` (unconstrained), `confidence: float` (unbounded), no
   `clarification` field, and a `SYSTEM_PROMPT` that instructs the model to use the old 9 fictional
   visual types (`line_chart`, `kpi_card`, `india_map`, etc.) rather than the 7 real ones. §3's
   contract is fully designed — this is purely a "hasn't been applied yet" gap, not an open design
   question.
9. **[Gap] `run_pipeline(news_context: list = [])` uses a mutable default argument** — a standard
   Python footgun (the default list object is shared across calls that don't pass their own). Not
   currently exploited since the function only reads it, but should become `news_context: list |
   None = None` with an `if news_context is None: news_context = []` guard the next time this
   function is touched — including when it's updated for gap #8 and for the `include_news` →
   `source_scope` migration (spec `07`).
10. **Model emits `"clarification": {"question": ..., "options": null}`** (seen live with Groq) —
    `ClarificationRequest.options` coerces `None` → `[]` via a `mode="before"` field validator
    (plus the prompt now says `options` must be an array, never null), so the clarification still
    renders as a question with no preset pills instead of failing validation into a generic
    fallback. Covered by `test_clarification_with_null_options_*` in `test_pipeline_contract.py`
    and `test_chat_api.py`.
11. **Structured narration fails but evidence exists** (seen live: transient Groq failures
    mid-conversation) — a plain-text prose rescue answers from the real rows/snippets/series
    (confidence 0.35, still through the visual guarantee) instead of the dead-end generic
    fallback; JSON-looking rescue text is discarded. With no evidence at all, the honest
    fallback stands. Covered by `test_prose_rescue_*`.

## 6. Acceptance Criteria

- [x] `visual_type` and `confidence` are constrained at the schema level (Literal + bounded Field,
      shown in §3) — applied to `langchain_pipeline.py` in B4 (was gap #8).
- [ ] `visual_type`'s 7 allowed values match `src/lib/schemas/visuals.ts` in the frontend exactly —
      any mismatch is a bug in one of the two places, not an acceptable drift. *(Backend now emits
      the 7 types; `Frontend/docs/type-contracts.md` §Chat was corrected to match, and the
      authoritative `visuals.ts` lands with frontend F0.)*
- [x] `PipelineOutput.clarification` is a working alternate response mode, not just a schema field
      — the pipeline's SYSTEM_PROMPT instructs the ask-don't-guess path (specs/10 §2, spec `11` §4);
      exercised via the route and unit tests. (Benchmarking's first concrete use lands with B6.)
- [x] An end-to-end route exists: user query → generate SQL (`05`) → execute → optionally fetch
      news (`07`) → `run_pipeline` → `PipelineOutput` returned to the frontend.
      *(`POST /chat`, B4; news fetching stays `own_data`-only until B7.)*
- [x] A dataset with 10,000+ rows does not silently blow the model's context window.
- [x] Decision-driven loop (FR8/FR9 hardening): judge verdict → narration → deterministic visual
      guarantee, prior-turn context from QueryLogs, anti-repeat clarification backstop. Covered by
      `TestDecisionLoop` (`test_pipeline_contract.py`) and the guarantee/follow-up e2e tests
      (`test_chat_api.py`).
- [x] Live hardening round 2 (FR10/FR11): LLM query framing + multi-query fan-out with dedupe and
      generic symbol resolution (`query_rewriter.py`, `web_search.py`); empty-completion retries for
      judge and narration; requested-shape handling end-to-end; numbered-snippet citations with
      phantom-marker stripping; machine-written `thinking` trace; sources-table guarantee for
      web-only answers. Covered by `test_query_rewriter.py`, `TestRobustnessLoop`, and the live-web
      citations e2e. Clarifications accept free-text replies as well as pills (frontend).
      *(`_truncate_rows` caps prompt rows at 50 with a summarizing note, plus the executor's SQL
      LIMIT.)*
