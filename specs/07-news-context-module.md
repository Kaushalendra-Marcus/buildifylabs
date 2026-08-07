# Spec 07 — News Context Module

**Status:** ❌ Not started. No code exists. This spec is forward-looking, based on design
discussion, to unblock future work — not to certify current behavior.
**Source files:** none yet.

---

## 1. Problem Statement

Answering "why did X happen" convincingly benefits from correlating a user's business data against
real-world news for their named company/industry (fuel prices, industry slowdowns, regulatory
changes). This is a planned enhancement layer on top of the core insight pipeline (spec `06`).

## 2. Functional Requirements (planned)

- **FR1:** User names a company/industry and opts in via a toggle to include news context in a
  given query.
- **FR2:** System fetches recent news via Google News RSS (free, no API key required) for that
  company/industry.
- **FR3:** News items are embedded (MiniLM, self-hosted, ~90MB) and upserted into a per-user
  `..._news` Pinecone namespace, kept separate from the user's static business-data namespace.
- **FR4:** News embeddings refresh on a TTL (originally discussed: 6 hours), cached via Redis —
  not re-fetched/re-embedded on every single query.
- **FR5:** At query time, if `include_news=True`, relevant news chunks are retrieved from Pinecone
  and passed into `run_pipeline`'s `news_context` argument (spec `06`).

## 3. API Contracts (proposed)

No dedicated CRUD API planned initially — this surfaces as parameters on the future `/chat`
endpoint:
```json
{ "query": "string", "include_news": true, "company_name": "string" }
```

## 4. Constraints

- Google News RSS returns headlines/summaries only. Full-article scraping was explicitly ruled out
  during design (legal gray area) — headline + summary text is the ceiling for this module.
- `PINECONE_API_KEY` and `REDIS_URL` are currently `Optional` in `config.py` — this module cannot
  be enabled until both are provisioned and made required.
- Refresh cadence should be per-active-user, not a global blind refresh-every-6h-for-everyone, to
  avoid burning embedding calls on inactive users. Not yet designed in detail.

## 5. Edge Cases & Error Handling (anticipated)

1. **No news coverage at all** for a small/local business name → must return an explicit "no
   relevant news found" signal, not an empty list that's indistinguishable from a bug.
2. **Stale/old articles outside a relevant date window** → needs an explicit recency filter on the
   RSS results.
3. **Pinecone free-tier vector cap (100K vectors)** reached across all users → needs a
   cleanup/eviction policy for old news namespaces — not yet designed.

## 6. Acceptance Criteria

- [ ] Not applicable yet — no implementation exists. This spec exists purely to define scope
      before work starts; acceptance criteria will be added once FR1–FR5 have a first
      implementation to test against.
