# Spec 06 — AI Insight & Visual Pipeline

**Status:** ⚠️ Partially implemented — LLM calling layer and structured-output pipeline are done;
not reachable from any HTTP route yet, and does not use the LangChain library despite the filename.
**Source files:** `app/services/llm/groq_service.py`, `app/services/llm/langchain_pipeline.py`

---

## 1. Problem Statement

The product's differentiator beyond raw charts is explaining **why** something happened in the
user's data, optionally correlating it with real-world news, and returning everything in one
strict, machine-readable shape the frontend can render without further parsing or guessing.

## 2. Functional Requirements

- **FR1:** Given a user query + business data + optional news context, return one structured JSON
  object matching `PipelineOutput`.
- **FR2:** Output must include: `answer`, `visuals[]` (each with `visual_type`, `chart_data`,
  `title`), `insights[]`, `summary`, `root_causes[]`, `recommendations[]`, `news_context[]`,
  `anomalies[]`, `confidence`.
- **FR3:** `visual_type` must be one of 9 supported types: `line_chart`, `bar_chart`, `pie_chart`,
  `kpi_card`, `heatmap`, `funnel_chart`, `india_map`, `anomaly_chart`, `ai_summary`.
- **FR4:** If the LLM's JSON is malformed or fails schema validation, degrade gracefully to a safe
  fallback `PipelineOutput` (empty lists, `confidence = 0.0`, a generic message) rather than raising
  to the caller.
- **FR5:** If Groq fails after 3 retries (with backoff), transparently fall back to a HuggingFace
  Inference API call before giving up entirely.
- **FR6:** News context is included in the prompt only when `include_news=True` is explicitly
  passed (user opt-in) — otherwise the LLM is told the user didn't request it.

## 3. API Contracts (internal — no HTTP surface yet)

**`generate_response(prompt, system_prompt, model, temperature, max_tokens) -> dict`**
✅ Done. Returns `{ "content": str, "usage": object|None, "source": "groq"|"huggingface" }`.
Retries Groq 3x with linear backoff, then falls back to HF.

**`stream_response(prompt, system_prompt, model) -> AsyncGenerator[str]`**
✅ Done. Streams Groq tokens. **No HF fallback on stream failure** — on a mid-stream Groq error it
yields a single static error string instead (streaming can't gracefully fail over mid-response).

**`run_pipeline(user_query: str, db_data: dict, news_context: list = [], include_news: bool = False) -> PipelineOutput`**
✅ Done, but **currently unreachable** — no route calls it yet. Expects `db_data` to already be
fetched by the caller (per spec `05`); does not fetch its own data.

```python
class VisualOutput(BaseModel):
    visual_type: str   # should be Literal[...] — see gap below
    chart_data: Dict
    title: str

class PipelineOutput(BaseModel):
    answer: str
    visuals: List[VisualOutput]
    insights: List[str]
    summary: str
    root_causes: List[str]
    recommendations: List[str]
    news_context: List[str]
    anomalies: List[str]
    confidence: float   # should be Field(ge=0.0, le=1.0) — see gap below
```

## 4. Constraints

- `max_tokens = 2000` for the structured pipeline call. The full JSON schema (9 possible visual
  types across several list fields) can plausibly approach or exceed this on complex answers —
  truncated JSON fails `extract_json` / Pydantic validation and silently degrades to the fallback
  response. Worth monitoring the `source` field and fallback-rate in logs once live.
- Temperature is fixed at `0.2` for the structured pipeline (favoring JSON reliability) vs. the
  `0.3` default on generic `generate_response` calls.
- The HF fallback model (`Mixtral-8x7B-Instruct`) is a plain instruction model, not tuned for this
  JSON schema the way the primary Groq/Llama call is prompted for — a fallback response is
  meaningfully more likely to fail `extract_json`/validation than a primary response. Accepted as
  "better than nothing," not schema-reliable.
- This module does not fetch its own data — `db_data` (spec `05`) and `news_context` (spec `07`)
  must be supplied by the caller.

## 5. Edge Cases & Error Handling

1. **Valid JSON but missing a required field** → `ValidationError` → fallback response
   (`confidence = 0.0`), logged.
2. **Non-JSON prose returned** → `extract_json` raises `ValueError` → fallback response, logged.
3. **[Gap] `visual_type` not constrained to the 9 allowed values.** It's a plain `str` in
   `VisualOutput`, not a `Literal`/`Enum` — an invalid value from the LLM currently passes schema
   validation and would reach the frontend, which must then defensively handle unknown types.
   **Fix:** constrain to `Literal["line_chart", "bar_chart", ...]`.
4. **[Gap] `confidence` not range-constrained.** Plain `float`, no bounds — a value like `1.4` or
   `-0.2` currently passes validation. **Fix:** `Field(ge=0.0, le=1.0)`.
5. **Both Groq and HF fail** → `hf_fallback` raises `RuntimeError`, which is caught by
   `run_pipeline`'s generic `except Exception` branch (not a dedicated branch) and returns the
   generic fallback message. Functionally acceptable, but confirm this is intentional rather than
   incidental — a dedicated log line for "both providers failed" would aid debugging in production.
6. **Very large `db_data`** (thousands of rows) → no truncation/summarization step exists before
   injecting it into the prompt. Risk of exceeding the model's context window on the *input* side
   before ever hitting the output `max_tokens` cap. Needs a row-count/size cap or a
   summarize-before-prompt step before production use.

## 6. Acceptance Criteria

- [ ] ≥95% of well-formed Groq responses parse successfully into `PipelineOutput` without hitting
      the fallback path (measurable via the `source` field in logs).
- [ ] `visual_type` and `confidence` are constrained at the schema level (closes gaps #3, #4).
- [ ] An end-to-end route exists: user query → generate SQL (`05`) → execute → optionally fetch
      news (`07`) → `run_pipeline` → `PipelineOutput` returned to the frontend.
- [ ] A dataset with 10,000+ rows does not silently blow the model's context window (gap #6
      addressed with a defined cap or summarization step).
