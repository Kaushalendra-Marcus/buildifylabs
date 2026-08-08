# Spec 06 — AI Insight & Visual Pipeline

**Status:** ⚠️ Partially implemented — LLM calling layer and structured-output pipeline exist; not
reachable from any HTTP route yet. **The `visual_type` contract below has changed** — it now
matches the 7 components actually built in the real frontend (`13-frontend-migration.md`) instead
of the 9 originally-speced types, which didn't correspond to anything actually built.
**Source files:** `app/services/llm/groq_service.py`, `app/services/llm/langchain_pipeline.py`
(needs updating to match this file — see §3)

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
- **FR6:** News context is included in the prompt only when `include_news=True` is explicitly
  passed (user opt-in) — otherwise the LLM is told the user didn't request it.
- **FR7 (new):** The pipeline can return a **clarifying question instead of a final answer**, per
  the ask-don't-guess pattern in `11-prediction-and-calculation.md` §4. When it does,
  `PipelineOutput.clarification` is populated (`{ question: str, options: list[str] }`) and the
  other answer fields are empty — the frontend renders this as a quick-pick prompt, and the user's
  choice becomes the next turn's input, not a fresh unrelated question.

## 3. API Contracts (internal — no HTTP surface yet)

**`generate_response(...)`** — superseded by `generate_completion(...)` in
`12-llm-orchestration.md` §4; update call sites accordingly when that spec is implemented. Until
then, the existing Groq→HF-only behavior stands as an interim implementation.

**`run_pipeline(user_query: str, db_data: dict, news_context: list = [], include_news: bool = False) -> PipelineOutput`**
✅ Core logic done, but **currently unreachable** — no route calls it yet, and its `visual_type`
values need updating to match FR3 below before it's wired up. Expects `db_data` to already be
fetched by the caller (per spec `05`); does not fetch its own data.

```python
class VisualOutput(BaseModel):
    visual_type: Literal["metric", "graph", "table", "comparison", "insight", "alert", "status"]
    props: Dict           # shape depends on visual_type — see src/lib/schemas/visuals.ts
                           # in the frontend for the authoritative per-type prop schema
    title: str

class ClarificationRequest(BaseModel):
    question: str
    options: List[str]

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

## 6. Acceptance Criteria

- [ ] `visual_type` and `confidence` are constrained at the schema level (Literal + bounded Field,
      shown in §3) — this closes what used to be listed as open gaps here.
- [ ] `visual_type`'s 7 allowed values match `src/lib/schemas/visuals.ts` in the frontend exactly —
      any mismatch is a bug in one of the two places, not an acceptable drift.
- [ ] `PipelineOutput.clarification` is a working alternate response mode, not just a schema field
      — at least one real pipeline path (competitor benchmarking, per `11`) exercises it.
- [ ] An end-to-end route exists: user query → generate SQL (`05`) → execute → optionally fetch
      news (`07`) → `run_pipeline` → `PipelineOutput` returned to the frontend.
- [ ] A dataset with 10,000+ rows does not silently blow the model's context window.
