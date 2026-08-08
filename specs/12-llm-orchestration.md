# 12 — Multi-LLM Orchestration

**Status:** ❌ Not started. Extends `app/services/llm/groq_service.py`'s existing Groq→HuggingFace
fallback pattern rather than replacing it — that pattern is the right foundation, it just needs more
providers and a bit of routing logic.

---

## 1. Problem Statement

A single-provider LLM setup (Groq only, HF as a weak fallback) is a real reliability and quality
risk once this product has real users: free-tier rate limits are real, and a fallback that's
noticeably worse at structured JSON output degrades the product exactly when the primary is under
load. The product has access to multiple providers (Groq, Gemini, other free/open-source APIs, and
a Claude API key) — this spec defines how they're orchestrated.

## 2. Where this lives

**Entirely in the FastAPI backend.** The frontend calls only the backend's `/chat` endpoint and
never talks to an LLM provider directly — no provider secrets ever reach the client, there's exactly
one place this logic lives, and adding or reordering a provider is a config change, not a rewrite
touching multiple codebases. (This also resolves what used to be a real architecture question: the
Next.js frontend previously had its own direct Groq integration, duplicating responsibility with the
backend — that gets consolidated into this single orchestration layer, see
`13-frontend-migration.md`.)

## 3. Design: cost-tiered cascade + two quality-critical routing points

Not a pure "always try cheapest first" chain, and not "always use the best model" either — route
based on where a wrong answer actually costs something, not on how difficult a task feels.

**Default path (most of the pipeline — intent parsing, general chat, formatting, statistics
narration):** ordered cascade through free-tier providers, moving to the next on failure/rate-limit:
`Groq → Gemini → open-source (via HF or similar)`. Claude is not in this default path — it costs
money, and most of the pipeline doesn't need the extra quality.

**Two deliberate Claude touchpoints**, because a wrong answer here is invisible to the user and
directly damages trust (see `10-trust-safety-compliance.md`):

- **NL→SQL generation** escalates to Claude automatically only when the cheaper model's generated
  query fails validation (`05-query-sql-safety.md`) or fails to execute — a reliability net on the
  riskiest step, not a blanket first choice.
- **Final root-cause/insight synthesis** (the actual trust-sensitive core of the product, per
  `10-trust-safety-compliance.md` §2) always goes to Claude.

This keeps Claude usage to roughly 1–2 calls per query, not the whole pipeline, while putting
quality exactly where being wrong is expensive. Per-user usage is already capped (`4` questions per
rolling 6-hour window, `100` lifetime — `02-plan-quota-enforcement.md`), so a single user can't run
up runaway cost even under this more generous policy.

## 4. API Contract (internal — no HTTP surface)

**`generate_completion(prompt, system_prompt, *, task: Literal["default","sql","synthesis"] = "default", ...) -> dict`**

- `task="default"` → cascade through the free-tier provider list in order.
- `task="sql"` → try the default cascade first; on validation/execution failure, retry once against
  Claude before giving up.
- `task="synthesis"` → call Claude directly, no cascade.
- Output: `{ "content": str, "source": "groq"|"gemini"|"<open-source-provider>"|"claude" }` — the
  `source` field is what makes fallback behavior observable in logs, same as the existing
  Groq/HF `source` field does today.

## 5. Constraints

- **Track per-provider recent-failure state** (e.g. "Groq failed in the last N minutes") so a
  currently rate-limited provider isn't retried on every single request — skip it for a short cool
  down window instead. Small addition, meaningfully cuts latency on a bad day.
- Each provider needs its own config var (`GROQ_API_KEY`, `GEMINI_API_KEY`, the open-source
  provider's key, `ANTHROPIC_API_KEY`) and its own client wrapper behind the same internal
  interface — providers should be swappable without touching calling code.
- Structured JSON reliability varies by provider — the existing gap in `06-ai-insight-pipeline.md`
  (constrain `visual_type`/`confidence` at the schema level) matters more now that a wider range of
  models produces the JSON, not just Groq/Llama.
- This spec does not change *what* gets asked of the LLM (`05`, `06`, `11` define that) — only
  *which provider* answers, and *when a stronger one is worth the cost*.

## 6. Acceptance Criteria

- [ ] `/chat` never fails outright just because Groq is down — the cascade reaches at least one
      working provider under a single-provider outage.
- [ ] The `source` field in logs makes it possible to see, after the fact, which provider actually
      answered each request and how often the cascade had to move past the first option.
- [ ] SQL generation retries against Claude specifically on validation/execution failure, not on
      every request.
- [ ] Final synthesis always goes to Claude — verifiable in logs (100% `source: "claude"` on that
      specific call).
- [ ] A currently-failing provider isn't retried on every request within its cool-down window.
