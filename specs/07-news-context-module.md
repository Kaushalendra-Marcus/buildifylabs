# Spec 07 — External Context: User-Directed Source Scope

**Status:** ❌ Not started. No code exists. This spec is forward-looking, based on design
discussion, to unblock future work — not to certify current behavior.
**Source files:** none yet.

> **Design history:** this module has gone through three interaction models. First, a simple
> opt-in toggle. Then, an automatic "always fetch live context unless the query is fully answerable
> from the user's own data" classifier — rejected because misclassification (guessing wrong about
> whether external context was needed) was identified as a real failure mode with no clean recovery.
> **This version replaces both with explicit, user-directed scope** — the user always controls
> whether an answer draws from their own data, the live internet, or both, the same way they'd
> naturally tell a human analyst "check my numbers" vs. "check what's happening in the news."

---

## 1. Problem Statement

Some questions are answerable purely from the user's own uploaded/saved business data (specs `04`,
`05`, `08`). Others need real-world external context — news, market conditions, industry trends —
that only the live internet has. The system should never silently guess which one a query needs.
Instead, the user explicitly directs where an answer should come from, per query, either by picking
an option or just saying it in their own words.

## 2. Functional Requirements

- **FR1:** Every query carries an explicit `source_scope`: `"own_data" | "live_web" | "both"`.
- **FR2:** Default is `"own_data"`. Live-web sources are only ever fetched when the user asks for
  them — one way or another (FR3 or FR4) — never by default, and never by silent inference from
  query content alone.
- **FR3:** The chat UI exposes a lightweight, persistent selector for `source_scope` next to the
  query input — the user sets it before asking, it applies to that query, and reasonably persists
  as the default for the next one until changed. This is a standing control, not a per-query
  interruption.
- **FR4:** The user can also just say it in plain words — "check the news on fuel prices this
  month," "compare this to what's happening in the market." If the query text clearly asks for
  external context while the selector is still on `"own_data"`, the pipeline must not silently
  ignore the words, and must not silently override the selector either. It reuses the existing
  clarifying-question mechanism (`06` FR7 / `PipelineOutput.clarification`) to confirm: *"Want me to
  also check live sources for this one?"* with quick-pick options — the same UI pattern already
  built for other ambiguous-scope moments (`11` §4), not a new one.
- **FR5:** When scope includes `live_web`, fetch relevant category-based web-scraped sources
  (business, funding, stocks, tech, healthcare — extensible list), cached via Redis on a TTL
  (proposed: 6 hours) so the same sources aren't re-scraped on every query in that window.
- **FR6:** When scope is `"both"`, the pipeline combines graph/vector retrieval over the user's own
  data (`04`/`05`/`08`) with the scraped live context **in the same call** — one merged answer, not
  two separate answers stitched together.
- **FR7:** `"live_web"` alone (no own-data retrieval at all) is a valid, complete choice — e.g.
  "what's the latest fuel price news" doesn't need the user's uploaded sales file.

## 3. API Contracts (proposed)

No dedicated CRUD API — this surfaces as parameters on the future `/chat` endpoint:
```json
{
  "query": "string",
  "source_scope": "own_data | live_web | both",
  "company_name": "string?"
}
```
`company_name` (or industry) is used to pick relevant scrape categories when `live_web` is in
scope. Asking for it on every query is poor UX — **open design question:** better to capture it
once as a profile-level field at signup/first-upload and default to it, letting a query override it
only when explicitly named.

## 4. Constraints

- Web-scraped sources must respect each site's `robots.txt` and Terms of Service. Only
  headline + short snippet is extracted, never full article bodies — same ceiling as before, now
  more consequential since this is a directly user-invokable mode rather than a background
  best-effort layer.
- Category source lists (business, funding, stocks, tech, healthcare, ...) must be configurable, not
  hardcoded — sources get swapped when a scraper breaks or a site blocks access.
- Rate-limit scraping per source (politeness delay + the cache TTL above) to avoid triggering
  anti-bot blocking.
- `PINECONE_API_KEY` and `REDIS_URL` are currently `Optional` in `config.py` — `live_web`/`both`
  scope cannot be enabled until both are provisioned and made required.

## 5. Edge Cases & Error Handling

1. **User picks `"live_web"` or `"both"` but the query is really only about their own numbers**
   (e.g. "what was my March revenue") → the live-web fetch simply returns nothing relevant and is
   omitted from the final answer — not an error, and not a reason to pad the response with
   irrelevant scraped content just because the scope allowed it.
2. **User picks `"own_data"` but has no uploaded data yet** → say so directly. Do not silently fall
   back to `live_web` without being asked — that's overriding the user's explicit choice in the
   other direction, which is exactly as wrong as ignoring it.
3. **Selector and free-text request disagree** (FR4) → clarifying quick-pick, never a silent
   decision either way.
4. **All scraping sources for the relevant category fail** → proceed with `own_data` context if any
   exists, and tell the user live sources were unavailable this time, rather than failing the whole
   response over an enrichment layer.
5. **`company_name`/industry unresolvable** (no profile value, none given, none inferable from the
   query) → ask once via the clarification mechanism rather than scraping generic/irrelevant
   categories.

## 6. Acceptance Criteria

- [ ] A query with `source_scope = "own_data"` never triggers a scrape or Pinecone-news call —
      verifiable by its absence from that request's trace.
- [ ] A query with `source_scope = "live_web"` or `"both"` triggers category-appropriate scraping.
- [ ] A free-text request for external context while the selector is on `"own_data"` triggers a
      clarifying quick-pick, not silent ignoring or silent overriding.
- [ ] `"both"` produces one merged answer referencing both sources, not two stitched-together
      answers.
- [ ] A single source's scrape failure never fails the overall response when other sources or
      `own_data` context are still available.
