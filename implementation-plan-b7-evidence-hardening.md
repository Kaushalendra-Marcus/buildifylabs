# Implementation Plan — Live-Web Evidence Hardening & Visual Guarantee Tuning

**Companion to `implementation-plan-master.md`. Slots in around Phase B7 (`specs/07`) and
extends Phase B4's visual guarantee (`specs/06` FR9). Written after a full read-through audit of
`Backend/` and `Frontend/` on 2026-09 — see "Audit findings" below before touching any code.**

**Who this is for:** a coding agent (Claude Code / opencode or similar) that will be told to
"implement this plan." It is written to be followed literally, phase by phase, without needing to
re-derive design decisions. **Do not skip ahead or combine phases.** Do not touch files outside
the "Files to touch" list of the phase you are on.

---

## 0. Audit findings (read this first — it changes how you should approach the work)

1. **The live-web retrieval flow the product wants already exists and is more sophisticated than
   a first read of `specs/00`/`specs/07`'s status table suggests.** `query_rewriter.py` already
   turns a raw chat message into 1–3 clean search queries via LLM. `web_search.py` already fans
   those out concurrently across Tavily + DuckDuckGo, resolves named entities to stock symbols,
   pulls structured Yahoo Finance / FRED / Wikipedia data, deep-reads pages via Tavily Extract, and
   caches results (Redis-or-memory, per-evidence-class TTL). `langchain_pipeline.py::ensure_visuals`
   already deterministically guarantees 1–5 grounded visuals (graph/table/comparison/metric/status)
   per answer instead of leaving an answer naked. `POST /chat` (`chat.py`) already wires
   `source_scope in ("live_web", "both")` to all of this, and it is covered by a real test
   (`test_chat_api.py::test_live_web_scope_uses_retrieved_web_context`).
2. **Several docs are stale and contradict the code.** `specs/00-overview.md`'s module table still
   marks module 7 as "❌ Not started"; `specs/07-news-context-module.md`'s own header says
   "⚠️ Partial... still open: FR3 (frontend selector)" even though F5 already shipped that
   selector; `Backend/app/routes/chat.py`'s docstring and `app/schemas/chat.py`'s docstring both
   say "gated/mocked until B7"; `Backend/CLAUDE.md` says "Not wired yet: ... Redis" even though
   `web_search_cache.py` uses Redis when `REDIS_URL` is set; `Frontend/src/features/chat/
   Composer.tsx`'s tooltip literally tells the user *"Not available yet — answers fall back to
   your own data"* for Live web / Both, which is not true of the backend behavior. **Phase 0 below
   fixes this before any behavior change**, so nobody (human or agent) re-derives wrong
   assumptions from stale docs mid-plan.
3. **Real, confirmed gaps** (not doc drift — actual missing behavior):
   - No context-size *budgeting* for retrieved web evidence. Today it's fixed-count/fixed-char
     truncation (`WEB_SEARCH_MAX_RESULTS` results per query, `EXTRACT_MAX_CHARS = 4000` chars per
     extracted page, `total_cap = WEB_SEARCH_MAX_RESULTS * len(search_queries)` slicing at the
     end of `search_web()`), not relevance-ranked selection against an actual size budget. This is
     the "batch karo jab context bada ho jaye" gap.
   - `specs/07` FR4 (a free-text request for external context while the selector is on
     `own_data` must trigger a clarifying quick-pick, never silent ignore/override) is **not
     implemented** — confirmed by reading `chat.py::_answer_request`, which only ever calls
     `search_web` when `request.source_scope in ("live_web", "both")`; there is no detection of
     "the query text is asking for the web while the scope says own_data."
   - The visual guarantee caps out at 4–5 synthesized visuals per answer (three separate literal
     slices: `synthesized[:3]`, `synthesized[:5]`, `synthesized[:4]` inside `ensure_visuals`), not
     a single tunable ceiling.
   - Only CSV upload is actually parsed (`app/services/data/parser.py` returns "not supported
     yet" for `.xlsx`/`.pdf`). If "apne upload docs se puchna" is meant to include PDFs/documents
     (not just spreadsheet-shaped business data), that is a materially separate, larger feature
     (needs chunking + embeddings + a vector store — `specs/04` and `specs/08` already describe it
     and both are explicitly deferred). **This plan does not build that** — see §7 (Not in this
     plan) for why, and what to do if you decide you want it.
4. **A structural risk that affects how every phase below must be implemented:**
   `Backend/app/services/llm/langchain_pipeline.py` is 289 KB / ~6,500 lines, and
   `Backend/app/services/data/comparison.py` is 144 KB / ~3,370 lines. Both are single files that
   have absorbed dozens of incremental patches. Every phase below is written to add *small, new,
   single-purpose files* and touch the two giant files only with minimal, narrow integration edits
   (a handful of lines each, at a named call site) — never a large edit inside them. This is not
   optional politeness; it is the difference between a coding agent making a safe, verifiable diff
   and one that silently breaks an unrelated code path buried elsewhere in a 6,500-line function.

### Non-negotiable constraints across every phase (re-read before each phase)

- **Generic, not hardcoded.** No new company names, industries, categories, or topic lists.
  Every new threshold is a named constant in `app/config.py` (env-overridable, sane default), the
  same pattern already used for `WEB_SEARCH_MAX_RESULTS` / `WEB_SEARCH_CACHE_TTL_SECONDS`. This
  must work for any business, any industry, any query shape — matches `specs/00` §1's own stated
  principle ("global and industry-agnostic by design").
- **The LLM never computes numbers.** Any new code that touches figures follows the existing
  invariant (`specs/11` §2): arithmetic in code, LLM only narrates/paraphrases.
- **Fail soft, never raise to `/chat`.** Every new function degrades to "skip this enhancement,
  keep the old behavior" on any error, exactly like the rest of `web_search.py` and
  `langchain_pipeline.py` already do. A bug in a new ranking/summarization helper must never turn
  into a broken chat response — it must fall back to today's behavior.
- **Citations must never break.** `context[i]` and `sources[i]` (or the merged-pairs list you are
  given) must stay 1:1 aligned through any reordering/filtering you add — the `[n]` citation
  system in `build_prompt`/`sanitize_citations` depends on this.
- **One phase at a time.** After each phase: run `python -m pytest` from `Backend/` and
  `npm run build && npm run lint && npm test` from `Frontend/`. Test counts must only go up, never
  down. Do not start the next phase until the current one's Definition of Done is fully green.
- **Update docs in the same change as the phase**, not as an afterthought: the relevant spec
  file's status line/acceptance checkboxes, and a new bullet in `STATUS.md`'s "Completed tasks"
  section written in the exact style already used there (bold phase name, "done, test-verified
  (...)" with real test counts, a short bullet list of what shipped, files touched).

---

## Phase 0 — Documentation reconciliation (no behavior change)

**Goal:** make every doc that describes live-web/`source_scope` match what the code actually does
today, *before* changing any behavior, so nobody (including the next phase) works from a wrong
premise.

**Files to touch (text-only edits, no logic changes):**
1. `specs/00-overview.md` — module map row 7: change status from "❌ Not started — deferred" to
   "⚠️ Partial — live retrieval implemented and tested (see `specs/07`); frontend copy still says
   otherwise (fixed in this plan's Phase 6)." Also fix §2's architecture diagram line
   `Frontend (Next.js)` → `Frontend (React 19 + Vite + react-router)` (the actual F0-locked stack —
   confirmed in `Frontend/package.json` and `Frontend/CLAUDE.md` §4).
2. `specs/07-news-context-module.md` — update the "Still open" list in the header: remove "FR3
   persistent scope selector (frontend)" (it shipped in F5 — `Composer.tsx` + `scope-store.ts`).
   Keep FR4 (disagreement quick-pick), category-based source lists, and Pinecone as genuinely
   still-open, and add a note that FR4 is closed by Phase 4 of this plan once it ships.
3. `Backend/app/routes/chat.py` — top-of-file docstring: replace "MVP `source_scope` = `own_data`
   only; `live_web`/`both` are deferred to B7" with an accurate one-line statement that `live_web`/
   `both` are implemented and exercised by `test_live_web_scope_uses_retrieved_web_context`.
4. `Backend/app/schemas/chat.py` — docstring: replace "gated/mocked until B7" similarly.
5. `Backend/CLAUDE.md` — §3 "Not wired yet" line: remove `Redis` (it is wired, optional,
   `web_search_cache.py`); keep `Pinecone`, `Neo4j`, `Razorpay SDK`. §1: mention that chat already
   supports `live_web`/`both`, not just `own_data`.
6. `Frontend/CLAUDE.md` — the F5 paragraph currently says "Live web/Both are B7-gated with a hint,
   never silently switched" — replace with an accurate description matching whatever Phase 6 of
   this plan lands (write this edit as part of Phase 6, not now — leave a `TODO(phase-6)` comment
   here for now so it isn't forgotten).
7. `Frontend/src/features/chat/Composer.tsx` — do **not** change the tooltip text yet (that is a
   product decision, made explicitly in Phase 6). For now, only fix the comment block at the top of
   the file (the `5.2` bullet says "gated until B7") to say "gated behind `ENABLE_LIVE_WEB_SCOPE`
   (Phase 6)" so the comment stops asserting something false.

**Definition of Done:** no code behavior changes; `git diff` for this phase touches only prose/
comments/docstrings; existing test suites pass unchanged (same counts as before this phase).

---

## Phase 1 — Config additions (foundation for every later phase)

**Goal:** add every new tunable this plan needs, in one place, before any phase that uses them.

**File to touch:** `Backend/app/config.py` — add these fields to `Settings` (same `Field(...,
env=...)` style already used in the file):

```python
# --- Live-web evidence budgeting (specs/07 hardening) ---

# Hard character budget for the ranked snippet pool that enters the
# synthesis prompt. A cheap proxy for a token budget (no tokenizer
# dependency) — see app/services/llm/context_budget.py::estimate_tokens
# for the exact heuristic. Multiple search queries x multiple providers
# can produce more snippets than any single call should see; this is the
# ceiling that turns "first N by arrival order" into "best-fitting subset
# by relevance."
MAX_EVIDENCE_CONTEXT_CHARS: int = 12000

# A single retrieved source (e.g. one full Tavily-Extract page) larger
# than this triggers map-reduce summarization (Phase 3) before it is
# added to the evidence pool, instead of being hard-truncated mid-sentence.
SUMMARIZE_TRIGGER_CHARS: int = 6000

# --- Visual guarantee (specs/06 FR9) ---

# Ceiling on how many deterministically-synthesized visuals
# ensure_visuals() may attach to one answer. Single source of truth —
# replaces three separate literal slices inside that function.
MAX_SYNTHESIZED_VISUALS: int = 6

# --- source_scope rollout kill switch (specs/07, Phase 6 of this plan) ---

# Independent of any code change: flip to false (env var, no redeploy of
# logic) if e.g. the Tavily free-tier monthly quota is at risk. When
# false, live_web/both requests answer honestly from own_data only (never
# a silent full failure) — see Phase 6.
ENABLE_LIVE_WEB_SCOPE: bool = Field(True, env="ENABLE_LIVE_WEB_SCOPE")
```

**Definition of Done:** `Settings` loads with these defaults with no `.env` changes required
(matches existing "optional until its module ships" pattern); `python -m pytest` from `Backend/`
still fully green (nothing references these yet).

---

## Phase 2 — Visual guarantee ceiling (quick, isolated win)

**Goal:** let `ensure_visuals()` return up to `MAX_SYNTHESIZED_VISUALS` (default 6) visuals
instead of the current mixed caps of 3/4/5, without changing *which* visual types get
synthesized or in what priority order.

**File to touch:** `Backend/app/services/llm/langchain_pipeline.py` — **only** the three literal
slice expressions inside `ensure_visuals` (search for them; do not change anything else in this
function):

- `output.visuals = list(output.visuals) + synthesized[:3]` (what-if path)
- `output.visuals = list(output.visuals) + synthesized[:5]` (validated historical path)
- `output.visuals = list(output.visuals) + synthesized[:4]` (web-only figures path)

Replace each `[:N]` with `[: get_settings().MAX_SYNTHESIZED_VISUALS]`. `get_settings` is already
imported in this file (it's used elsewhere for the Groq model config) — confirm the import exists
at the top; add `from app.config import get_settings` only if it is genuinely missing.

Do not touch anything else in `ensure_visuals` — no reordering of which visual gets synthesized
first, no changes to the gating/provenance/grounding logic above and below these three lines.

**Tests to add** — `Backend/tests/test_pipeline_contract.py`:
- A new test that builds a fixture with enough validated evidence to make *more than 5* visuals
  eligible (e.g. `price_history` for 2 entities + `financial_history` revenue + net_income + a
  margin-eligible pair — reuse the existing historical-comparison-gate fixtures in this file as a
  starting point) and asserts `len(output.visuals) == 6` (or whatever the true ceiling for that
  fixture is, up to `MAX_SYNTHESIZED_VISUALS`), not silently capped at 5.
- A test that overrides `MAX_SYNTHESIZED_VISUALS` via `monkeypatch` to `2` and asserts the same
  fixture now yields exactly 2 — proves the constant is actually load-bearing, not dead code.

**Definition of Done:** both new tests pass; full existing suite unchanged in behavior (a query
that only ever produced ≤4 visuals still produces the same ≤4 — this phase only *raises the
ceiling*, it does not manufacture new visuals from nothing).

---

## Phase 3 — Evidence ranking + budget (the core "batching" request)

**Goal:** replace blind count-based truncation of retrieved web snippets with **relevance-ranked
selection against a character budget** — the standard retrieval pattern (rank, then fit-to-budget)
instead of "keep the first N that arrived." This directly answers the original ask: "jab scrap
hoga to context bada ho jaye to batch/best method use karo."

**New file:** `Backend/app/services/llm/context_budget.py`

```python
"""Generic evidence-context budgeting for the live-web pipeline (specs/07
hardening). Nothing here is topic/company/category-specific — every
threshold is a config constant (app/config.py), and ranking uses only
signals every provider result already carries (provider relevance score,
publish date, term overlap with the query).

Never raises. Every function degrades to a safe, deterministic default on
bad input (empty list, non-string text, etc.) so a bug here can only ever
make evidence selection worse, never crash a chat request.
"""
import re
from datetime import datetime, timezone
from typing import Optional

CHARS_PER_TOKEN_ESTIMATE = 4  # heuristic; intentionally no tokenizer dependency


def estimate_tokens(text: str) -> int:
    """Cheap token-count proxy (chars / 4). Good enough for budgeting;
    never used for anything that needs to be exact."""
    return max(0, len(text or "")) // CHARS_PER_TOKEN_ESTIMATE


def _query_terms(query: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))


def _term_overlap_score(text: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    text_terms = set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))
    if not text_terms:
        return 0.0
    return len(text_terms & query_terms) / len(query_terms)


def _recency_score(published_date: Optional[str]) -> float:
    """0..1, newer = higher. Missing date (e.g. every DuckDuckGo result)
    gets a NEUTRAL 0.4, never a penalty -- a dated-but-stale Tavily hit
    must not automatically outrank an undated-but-relevant DDG hit."""
    if not published_date:
        return 0.4
    try:
        parsed = datetime.fromisoformat(str(published_date)[:10])
        days_old = (datetime.now(timezone.utc).date() - parsed.date()).days
        if days_old <= 7:
            return 1.0
        if days_old <= 30:
            return 0.8
        if days_old <= 180:
            return 0.6
        if days_old <= 365:
            return 0.4
        return 0.2
    except Exception:
        return 0.4


def rank_snippet_pairs(
    texts: list[str], sources: list[dict], query: str
) -> tuple[list[str], list[dict]]:
    """Stable-sort (text, source) PAIRS together by a generic relevance
    score: provider score (Tavily's own 0..1 relevance, when present) +
    recency + query-term overlap. texts and sources MUST be the same
    length and already 1:1 aligned by position (as search_web produces
    them) -- this function preserves that alignment, it only reorders
    both lists identically. Never raises: on any error, returns the
    inputs unchanged (today's order).
    """
    try:
        if len(texts) != len(sources):
            return texts, sources
        terms = _query_terms(query)
        indexed = list(enumerate(zip(texts, sources)))

        def _score(item) -> float:
            _idx, (text, source) = item
            try:
                provider_score = float(source.get("score") or 0.0)
            except (TypeError, ValueError):
                provider_score = 0.0
            recency = _recency_score(source.get("published_date"))
            overlap = _term_overlap_score(text, terms)
            return provider_score * 0.4 + recency * 0.3 + overlap * 0.3

        ranked = sorted(indexed, key=_score, reverse=True)
        return (
            [text for _i, (text, _s) in ranked],
            [source for _i, (_t, source) in ranked],
        )
    except Exception:
        return texts, sources


def fit_pairs_to_budget(
    texts: list[str], sources: list[dict], max_chars: int
) -> tuple[list[str], list[dict], int]:
    """Greedily keep (text, source) pairs in the given order while the
    running character total stays under max_chars. Assumes the caller has
    already ranked best-first (rank_snippet_pairs) -- this function does
    not re-rank, it only decides where to stop. Returns
    (kept_texts, kept_sources, dropped_count). Never raises.
    """
    try:
        if len(texts) != len(sources):
            return texts, sources, 0
        kept_texts: list[str] = []
        kept_sources: list[dict] = []
        used = 0
        for text, source in zip(texts, sources):
            cost = len(text or "") + 20  # small per-item overhead
            if used + cost > max_chars:
                break
            kept_texts.append(text)
            kept_sources.append(source)
            used += cost
        dropped = len(texts) - len(kept_texts)
        return kept_texts, kept_sources, dropped
    except Exception:
        return texts, sources, 0
```

**Integration point:** `Backend/app/services/web_search.py`, inside `search_web()`, at the final
assembly block (search for `total_cap = settings.WEB_SEARCH_MAX_RESULTS * max(1, len(search_queries))`).
Today it reads:

```python
total_cap = settings.WEB_SEARCH_MAX_RESULTS * max(1, len(search_queries))
structured_texts = _dedupe_texts(...)
context = structured_texts + merged_texts[:total_cap]
sources = (
    market_sources + fundamentals_sources + macro_sources
    + price_history_sources + financial_history_sources
    + knowledge_sources + merged_sources[:total_cap]
)
```

Change **only** the handling of `merged_texts`/`merged_sources` (the generic snippet pool — do not
touch `structured_texts`/`market_sources`/etc., those are already small and always kept):

```python
from app.services.llm.context_budget import fit_pairs_to_budget, rank_snippet_pairs

...

ranked_texts, ranked_sources = rank_snippet_pairs(
    merged_texts, merged_sources, query_text or ""
)
budget_texts, budget_sources, dropped = fit_pairs_to_budget(
    ranked_texts, ranked_sources, settings.MAX_EVIDENCE_CONTEXT_CHARS
)
if dropped:
    research_notes.append(
        f"Trimmed {dropped} lower-relevance web result(s) to stay within "
        "the evidence budget; the highest-relevance/most-recent results "
        "were kept."
    )

context = structured_texts + budget_texts
sources = (
    market_sources + fundamentals_sources + macro_sources
    + price_history_sources + financial_history_sources
    + knowledge_sources + budget_sources
)
```

Remove the now-unused `total_cap` slicing on `merged_texts`/`merged_sources` (keep the
`total_cap` variable only if something else in the function still reads it — check before
deleting). `research_notes` already exists in this function (used by the second-pass financial
research logic) — append to the same list, don't create a new field.

**Why this is safe:** `rank_snippet_pairs` and `fit_pairs_to_budget` both fail closed to "return
input unchanged" on any error, so a bug in this new code degrades to exactly today's un-ranked
behavior (still correct, just not improved) rather than breaking the response. `research_notes`
already flows into the prompt as an honest disclosure (same pattern as `macro_note`), so the model
can say "some lower-relevance results were left out" instead of silently narrating a partial view.

**Tests to add** — new file `Backend/tests/test_context_budget.py`:
- `estimate_tokens`: empty string → 0; `"a" * 400` → 100.
- `rank_snippet_pairs`: given 3 synthetic (text, source) pairs where one has a high
  `source["score"]`, one has a recent `published_date`, and one matches none of the query terms —
  assert the low-relevance one sorts last; assert texts/sources stay paired (same source ends up
  next to the same text after reordering); assert a length mismatch input returns unchanged
  (no exception).
- `fit_pairs_to_budget`: 5 short items well under budget → all 5 kept, `dropped == 0`; 5 items
  where item 4 pushes over budget → items 1–3 kept, `dropped == 2`; `max_chars=0` → `dropped ==
  len(items)`, no exception.
- Update `Backend/tests/test_chat_api.py` (or add a focused new test near
  `test_live_web_scope_uses_retrieved_web_context`): monkeypatch `search_web`'s underlying snippet
  fetch (or call `search_web` directly in a unit test with a monkeypatched `_snippets_for_query`)
  to return more synthetic results than `MAX_EVIDENCE_CONTEXT_CHARS` can hold, and assert (a) the
  returned `WebSearchResult.context` total character length stays under the budget, and (b)
  `research_notes` contains a "Trimmed" entry.

**Definition of Done:** all new tests pass; `test_live_web_scope_uses_retrieved_web_context` and
every other existing live-web test still pass unchanged (this phase changes *ordering/selection*,
not the shape of `WebSearchResult`).

---

## Phase 4 — Long-source summarization (map-reduce for oversized single sources)

**Goal:** when a single retrieved source (mainly a full Tavily-Extract page, or a deep-read
recommendation-page fetch) is larger than `SUMMARIZE_TRIGGER_CHARS`, compress it via batched LLM
summarization instead of hard-truncating it mid-sentence at `EXTRACT_MAX_CHARS`. This is the
literal "content bada ho to LLM ko batch mein bhejo" mechanism.

**New file:** `Backend/app/services/llm/evidence_summarizer.py`

```python
"""Map-reduce compression for one oversized retrieved source (specs/07
hardening). Only triggers above app.config.Settings.SUMMARIZE_TRIGGER_CHARS
-- short sources are returned untouched with zero extra latency/cost, the
same behavior as today.
"""
import logging
from typing import Optional

from app.services.llm.groq_service import generate_response
from app.config import get_settings

logger = logging.getLogger(__name__)

SUMMARIZE_CHUNK_CHARS = 3000
# Bounds latency/cost: a 4-chunk cap covers ~12,000 chars of source content
# in at most 4 LLM calls, running concurrently (caller's responsibility).
SUMMARIZE_MAX_CHUNKS = 4

_CHUNK_SYSTEM_PROMPT = (
    "You compress source text for a research pipeline. You do NOT answer "
    "the user's question. Given one chunk of a longer source and the "
    "user's question for context, extract ONLY the facts, numbers, names, "
    "and dates in this chunk that are relevant to the question. Never "
    "invent anything not present in the chunk. Never add opinion or "
    "commentary. Return plain text, at most 120 words. If nothing in this "
    "chunk is relevant, return exactly: NOTHING_RELEVANT."
)


def _chunk_text(text: str, chunk_chars: int, max_chunks: int) -> list[str]:
    text = text or ""
    chunks = [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    return chunks[:max_chunks]


async def summarize_long_text(
    text: str,
    query: str,
    *,
    chunk_chars: int = SUMMARIZE_CHUNK_CHARS,
    max_chunks: int = SUMMARIZE_MAX_CHUNKS,
) -> str:
    """Map-reduce compression of one long source. Splits into at most
    max_chunks chunks, summarizes each (fast model, temperature 0, strict
    no-invention prompt) focused on `query`, then joins the non-empty
    summaries. On ANY failure (LLM error, empty completions, etc.) returns
    the original text truncated to chunk_chars * max_chunks -- exactly
    today's EXTRACT_MAX_CHARS-style behavior, so this can only ever be a
    strict improvement over the status quo, never a regression.
    """
    settings = get_settings()
    fallback = text[: chunk_chars * max_chunks]
    try:
        chunks = _chunk_text(text, chunk_chars, max_chunks)
        if not chunks:
            return fallback
        import asyncio

        async def _summarize_chunk(chunk: str) -> Optional[str]:
            try:
                result = await generate_response(
                    prompt=f"User's question (context only):\n{query}\n\n"
                    f"Source chunk:\n{chunk}",
                    system_prompt=_CHUNK_SYSTEM_PROMPT,
                    model=settings.groq_fast_model,
                    temperature=0.0,
                    max_tokens=250,
                )
                content = (result.get("content") or "").strip()
                if not content or content == "NOTHING_RELEVANT":
                    return None
                return content
            except Exception as exc:
                logger.warning("Chunk summarization failed, skipping chunk: %s", exc)
                return None

        summaries = await asyncio.gather(*(_summarize_chunk(c) for c in chunks))
        kept = [s for s in summaries if s]
        if not kept:
            return fallback
        return " ".join(kept)
    except Exception as exc:
        logger.warning("Long-source summarization failed, using truncation: %s", exc)
        return fallback
```

**Integration points** — `Backend/app/services/web_search.py`:

1. `_tavily_extract()` — after `content = str(results[0].get("raw_content", "") or "").strip()`,
   change:
   ```python
   trimmed = content[:EXTRACT_MAX_CHARS]
   ```
   to:
   ```python
   if len(content) > get_settings().SUMMARIZE_TRIGGER_CHARS:
       from app.services.llm.evidence_summarizer import summarize_long_text
       trimmed = await summarize_long_text(content, query)
   else:
       trimmed = content[:EXTRACT_MAX_CHARS]
   ```
   (`get_settings` is already imported at the top of `web_search.py`.)

2. The recommendation deep-read loop (search for `wants_recommendation_extract` inside
   `search_web()`, the block that calls `_tavily_extract` for up to `_RECOMMENDATION_EXTRACT_URLS`
   URLs) needs no separate change — it already calls `_tavily_extract`, so it inherits this
   behavior automatically. Confirm this by reading that block before assuming it's covered.

**Tests to add** — new file `Backend/tests/test_evidence_summarizer.py`:
- Text shorter than `chunk_chars * max_chunks` with a mocked `generate_response` that raises if
  called at all → assert it's never called, and the returned text equals the input unchanged
  (cost-control: short sources must not pay for summarization).

  *(Note: `summarize_long_text` always chunks/calls today as written above — if you want the
  "don't call the LLM for short text" guarantee, add that check inside `summarize_long_text`
  itself: `if len(text) <= chunk_chars: return text` before chunking. Add this line — it's a
  correct optimization the design above is missing; add it and update the docstring to mention
  it.)*
- A long synthetic text (e.g. 10,000 chars) with a mocked `generate_response` returning a fixed
  short string per call → assert the result is the joined summaries, not the raw truncated text,
  and that at most `SUMMARIZE_MAX_CHUNKS` calls were made.
- A mocked `generate_response` that raises every time → assert the function returns
  `text[:chunk_chars*max_chunks]` (the exact fallback), never raises.
- A mocked `generate_response` that returns `"NOTHING_RELEVANT"` for every chunk → assert fallback
  truncation is used (not an empty string).
- `Backend/tests/test_web_search.py` (if it doesn't exist, check for the right existing test file
  covering `web_search.py` first) — a new test that a Tavily Extract result over
  `SUMMARIZE_TRIGGER_CHARS` calls `summarize_long_text` (monkeypatch it) instead of raw slicing.

**Definition of Done:** new tests pass; existing Tavily Extract tests (short content path) still
pass unchanged; no new latency/LLM calls for any source under `SUMMARIZE_TRIGGER_CHARS`.

---

## Phase 5 — Scope-disagreement clarification (`specs/07` FR4)

**Goal:** when `source_scope == "own_data"` but the query text is clearly asking for external/live
information, ask a clarifying quick-pick instead of silently ignoring the request or silently
switching scope — closing the one functional-requirement gap in `specs/07` that isn't just doc
drift.

**New constant + function** — add to `Backend/app/services/llm/query_rewriter.py` (same file that
already has the analogous `TIME_SENSITIVE_RE` / `is_time_sensitive_query` deterministic-regex
pattern — follow that exact style):

```python
# Explicit external-context request cues (specs/07 FR4). Deliberately a
# modest, generic phrase list -- not a topic/company whitelist. Any
# question can trigger this regardless of what it's about.
EXTERNAL_CONTEXT_RE = re.compile(
    r"\b(check the news|check live|search the web|look (this |it )?up online|"
    r"what'?s happening (in|with) the (news|market)|current market|"
    r"latest news on|what are people saying|check online|"
    r"compare (this|that|it) to the (news|market))\b",
    re.IGNORECASE,
)


def wants_external_context(text: str) -> bool:
    """Deterministic gate for specs/07 FR4: true when the raw query text
    is explicitly asking for live/external information, independent of
    the current source_scope selector."""
    return bool(text and EXTERNAL_CONTEXT_RE.search(text))
```

**Integration point** — `Backend/app/routes/chat.py`, inside `_answer_request`, right after
`plan_query` is computed (the clarification-merge block) and before the `own_data`/`both`
"require uploaded data" check. Add:

```python
from app.services.llm.query_rewriter import wants_external_context

...

if (
    request.source_scope == "own_data"
    and wants_external_context(plan_query)
    and prior_clarification != EXTERNAL_CONTEXT_CLARIFICATION_QUESTION
):
    output = PipelineOutput(
        answer="", visuals=[], insights=[], summary="",
        root_causes=[], recommendations=[], news_context=[],
        anomalies=[], confidence=0.0,
        clarification=ClarificationRequest(
            question=EXTERNAL_CONTEXT_CLARIFICATION_QUESTION,
            options=["Yes, check live sources too", "No, just my data"],
        ),
    )
    return await _log_and_return(
        db, user.id, request.query, output, time.monotonic() - started
    )
```

Define `EXTERNAL_CONTEXT_CLARIFICATION_QUESTION = "Want me to also check live sources for this
one?"` as a module-level constant in `chat.py` (needed so the anti-repeat check above can compare
against it by value).

**Anti-repeat requirement:** `STATUS.md` documents an existing "anti-repeat backstop (same
question twice → one best-effort answer)" for the FR8 judge's clarifications. Before writing the
check above, **find that existing mechanism** (search `langchain_pipeline.py` for how
`prior_clarification` is compared against a new clarification to detect a repeat — likely inside
the judge/decision logic) and reuse the *same* comparison approach here, not a new one. If the
existing mechanism is keyed on exact string equality of the question, the check shown above
(`prior_clarification != EXTERNAL_CONTEXT_CLARIFICATION_QUESTION`) is consistent with it. If it's
more sophisticated (fuzzy match via `difflib`, which is imported in `langchain_pipeline.py`), match
that instead so there's one anti-repeat convention, not two.

**When the user answers "Yes, check live sources too":** the next turn's `request.query` will be
that literal string with `source_scope` still `"own_data"` (the frontend doesn't know to flip the
selector — quick-pick answers are sent as plain user messages, per the existing clarification
pattern in `ClarificationMessage.tsx`). This means `wants_external_context` must also match on
`"Yes, check live sources too"` itself, or — cleaner — special-case it: if the user's literal reply
equals that exact option string, treat this turn as `source_scope = "both"` for evidence-gathering
purposes only (don't require the frontend to change). Implement this as a second, narrow check in
`_answer_request`: `effective_scope = "both" if request.query.strip() ==
"Yes, check live sources too" else request.source_scope`, and use `effective_scope` everywhere
`request.source_scope` currently drives branching in `_answer_request`. Do not change
`request.source_scope` itself (it's the logged/persisted value).

**Tests to add** — `Backend/tests/test_chat_api.py`:
- `test_own_data_scope_with_external_request_asks_clarification`: seed a user with uploaded data,
  POST `/chat` with `{"query": "check the news on fuel prices this month", "source_scope":
  "own_data"}`, assert the response has a non-null `clarification` with the expected question and
  the two options, and `confidence == 0.0`.
- `test_own_data_scope_plain_question_does_not_clarify`: same setup, query
  `"What is the average revenue?"`, `source_scope: "own_data"` → assert **no** clarification (must
  not regress the existing happy path — this is the single most important regression test for this
  phase).
- `test_external_context_yes_reply_triggers_live_search`: simulate the two-turn flow — first turn
  triggers the clarification (as above), second turn POSTs `{"query": "Yes, check live sources
  too", "source_scope": "own_data"}` with `search_web` monkeypatched (same pattern as
  `test_live_web_scope_uses_retrieved_web_context`) — assert the mocked `search_web` **was called**
  for this second turn even though `source_scope` was still `"own_data"`.
- `Backend/tests/test_query_rewriter.py` (find or create) — unit tests for `wants_external_context`
  directly: positive cases ("check the news on X", "what's happening in the market", "search the
  web for Y") and negative cases ("what was my march revenue", "show me a bar chart") — assert
  correct True/False, no false positives on plain own-data questions.

**Definition of Done:** all four new/updated backend tests pass; the second regression test
(`test_own_data_scope_plain_question_does_not_clarify`) is the gate — if it fails, the regex in
`EXTERNAL_CONTEXT_RE` is too broad and must be narrowed before merging.

---

## Phase 6 — Turn the feature on for real users (product decision + copy fix)

**Goal:** stop telling users the feature is unavailable when it isn't, while giving you an
explicit, instant off-switch independent of a redeploy (relevant because `WEB_SEARCH_API_KEY`
(Tavily) is on a free tier with a monthly search quota — turning this on for all real users is a
usage decision, not just an engineering one).

**Decision this phase assumes (change if you disagree):** `ENABLE_LIVE_WEB_SCOPE` defaults to
`true` (set in Phase 1) — i.e., ship it on, because the backend has already been treating it as
live and tested as such; the only thing actually gating it today is stale frontend copy. If you'd
rather keep it off until you've watched Tavily usage for a while, set `ENABLE_LIVE_WEB_SCOPE=false`
in `Backend/.env` — no code change needed, this phase's kill-switch wiring works either way.

**Backend file to touch:** `Backend/app/routes/chat.py`, `_answer_request` — right after
`effective_scope` is computed (Phase 5) or, if Phase 5 wasn't done first, right after
`plan_query` is computed:

```python
from app.config import get_settings as _get_chat_settings  # or reuse existing import

if effective_scope in ("live_web", "both") and not _get_chat_settings().ENABLE_LIVE_WEB_SCOPE:
    logger.info("Live-web scope requested but ENABLE_LIVE_WEB_SCOPE is false; using own_data only.")
    effective_scope = "own_data" if effective_scope == "both" else "own_data"
    # both -> own_data still answers from the user's data; live_web alone with no
    # data falls through to the existing "you haven't uploaded any data yet" message,
    # which is the correct, honest behavior (never a silent full failure).
```

Use `effective_scope` (not `request.source_scope`) for every branch in `_answer_request` that
currently reads `request.source_scope` to decide whether to run `plan_tools`/`search_web`, and for
the "does the user need uploaded data" check. Do not change what gets logged to `QueryLogs` (keep
logging the user's actual requested `request.source_scope`, for honest analytics about what users
are asking for even while it's switched off).

**Frontend files to touch:**
1. `Frontend/src/features/chat/Composer.tsx` — the `title` attribute on the scope segment button:
   ```tsx
   title={
     segment.value === 'own_data'
       ? 'Questions are answered from your uploaded data.'
       : 'Questions are answered using live web search and your uploaded data.'
   }
   ```
   Update the top-of-file comment block's `5.2` bullet to say "Live web / Both are live, gated only
   by the backend's `ENABLE_LIVE_WEB_SCOPE` switch" — remove "gated until B7."
2. `Frontend/src/features/chat/composer.css` — check whether there's a visually-disabled/greyed-out
   style keyed to the live-web/both segments specifically; if so, remove it (the segments should
   look identically selectable to "Your data").
3. `Frontend/src/features/chat/Composer.test.tsx` — update any assertion that currently checks for
   the "Not available yet" tooltip text to check for the new text instead.
4. `Frontend/CLAUDE.md` — resolve the `TODO(phase-6)` left in Phase 0: replace "Live web/Both are
   B7-gated with a hint, never silently switched" with an accurate description (live, backend
   feature-flagged).

**Docs to update in the same change:**
- `specs/07-news-context-module.md` header status → "✅ Implemented" (FR1, FR2, FR3, FR4, FR6, FR7
  all closed by this plan; FR5's category-based lists remain a deliberate design deviation — note
  that the product uses generic entity/intent detection instead of fixed category lists, which is
  a superset of what FR5 asked for, not a gap).
- `specs/00-overview.md` module map row 7 → "✅ Implemented" (or "⚠️ Partial — Pinecone-backed
  vector retrieval for uploaded documents is a separate, larger initiative, see §7 of the B7
  evidence-hardening plan").
- `STATUS.md` — new "Completed tasks" bullet in the house style summarizing Phases 1–6 with real
  test counts, same as every other bullet in that file.

**Definition of Done:** with `ENABLE_LIVE_WEB_SCOPE=true` (the default), a manual test — sign in,
select "Live web," ask a real current-events question — returns a real answer with sources and at
least one visual (or an honest "no results" if genuinely nothing was found), not a silent
own-data-only fallback. With `ENABLE_LIVE_WEB_SCOPE=false`, the same flow answers from `own_data`
only and does not attempt any network call to Tavily/DuckDuckGo (verify via logs).

---

## 7. Not in this plan (explicitly out of scope — needs your decision, not the agent's)

**Document/PDF question-answering over uploaded files.** The product today answers "apne data se"
questions via NL→SQL over a structured per-user table populated from CSV. If you want users to be
able to upload and ask questions about unstructured documents (PDFs, reports, contracts — not
spreadsheet-shaped business data), that is a different retrieval architecture entirely: parse →
chunk → embed → store in a vector index (Pinecone, per `specs/04`/`specs/08`) → retrieve →
synthesize. Both specs already describe this and both are explicitly deferred pending a vector
store decision. This is a multi-week initiative on its own, not a tuning pass on the existing
pipeline, and bundling it into this plan would make every phase above riskier for no reason (it
touches upload, storage, and a brand-new retrieval path, none of which the phases above need). If
you want it, ask for a dedicated plan for it — don't fold it into this one.

**Multi-LLM provider cascade (`specs/12`, Phase B5 of the master plan).** Genuinely not started
(only Groq multi-key rotation + a Hugging Face fallback exist today, which is explicitly documented
as the interim stand-in for it) — correctly out of scope here; it's a reliability project, not an
evidence-quality one.

---

## 8. Suggested execution order

Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6, strictly in that order, each
phase's Definition of Done fully green before starting the next. Phase 2 is the fastest, lowest-
risk win if you want to see something ship quickly; Phase 3 is the one that most directly answers
the original "batch the scraped context" ask and should not be skipped or reordered after Phase 4,
since Phase 4's summarizer feeds sources into the same pool Phase 3 ranks and budgets.
