# 10 — Trust, Safety & Compliance Requirements

> Status: 📋 Requirements — not yet implemented, tracked here so they get built alongside the
> features they apply to rather than retrofitted after launch. Cross-references `05` and `06` for
> the technical gaps this file adds product/legal context to.

## 1. Why this matters more than usual for this product

This product hands a non-technical business owner an LLM-generated interpretation of their own
sales/financial data, including causal claims about *why* something happened. Two things make the
stakes higher than a typical AI feature: (a) a confidently wrong number can directly cause a bad
business decision, and (b) Indian SMB communities are tight-knit — a reputation for "gave me a wrong
number" travels fast and is hard to undo. Separately, the data itself is often personal data
(customer names, emails, phone numbers in sales records) subject to Indian data-protection law
starting now, not eventually. This file covers both.

## 2. AI output trust requirements

These apply once `specs/06-ai-insight-pipeline.md`'s pipeline is actually wired to a route
(currently unreachable — see `Backend/docs/known-gaps.md`). Build them in from the start rather than
adding them after the fact; retrofitting "show your work" onto an already-shipped chat UI is much
more disruptive than designing for it up front.

- **Every insight and chart must be traceable to the underlying SQL query and the raw data slice it
  came from.** The frontend should always offer a way to see "here's the query that produced this"
  — not necessarily shown by default, but never hidden entirely. This is the single most effective
  trust-building feature available here, because it turns "trust the AI" into "verify the AI," which
  is a much easier bar to clear.
- **Causal language must be hedged, not asserted.** "A possible contributing factor" and "correlates
  with" instead of "the reason was" or "this caused." The pipeline's `root_causes` and
  `recommendations` fields (per the `PipelineOutput` shape in `specs/06`) are exactly where this
  matters most — a business owner acting on an assumed-certain wrong cause is the worst-case outcome
  this product could produce.
- **Surface the confidence score once it's meaningful.** `PipelineOutput.confidence` exists today
  but is an unbounded `float` with no guarantee the LLM actually returns something in a sane range
  (`specs/06` edge case, also in `Backend/docs/known-gaps.md`). Fix the schema constraint
  (`Field(ge=0.0, le=1.0)`) before building any UI that displays it, or a garbage value will render
  as if it were meaningful.
- **Give the user a way to flag a wrong or misleading answer**, and make that flag land somewhere
  reviewed, not a black hole. This is also the mechanism that makes the next point possible.
- **Wire `QueryLogs` writes.** The table already exists (`Backend/app/db/models/query_logs.py`) but
  nothing writes to it yet. Once the `/chat` route exists, log every query + response pair (with the
  flag state from the point above). This is the cheapest, highest-leverage thing to build for
  actually improving prompt/schema quality over time — without it, you're iterating on guesses about
  what people ask and where the model fails, which is the single most important thing to actually
  know for a product like this.
- **Ask, don't guess, when the pipeline lacks enough information to answer well.** Formalized in
  `specs/06` FR7 (`PipelineOutput.clarification`) and first used concretely in
  `11-prediction-and-calculation.md` §4 (competitor benchmarking) — but the principle applies
  anywhere in the pipeline, not just there. A wrong-but-confident answer is worse than a question
  back to the user; this is the single biggest lever against the hallucination risk this whole file
  exists to manage.

## 3. Security requirement — blocking, not backlog

`specs/05-query-sql-safety.md` and `Backend/docs/known-gaps.md` both already flag this, restated
here because it's a launch blocker, not a normal backlog item: **no automatic user-scoping exists on
generated SQL today.** Nothing currently guarantees a generated query only reads the requesting
user's own uploaded data. For a product asking real businesses to upload real sales and customer
data, this has to close before any user outside the founding team's own test data touches the
system — full stop, before growth work, before additional features.

**A concrete instance of exactly this class of risk already happened once in this project**, worth
keeping in mind as more than a hypothetical: a Tambo AI API key was found committed in a `.env` file
in the Next.js frontend (`13-frontend-migration.md` §4), exposed client-side via a
`NEXT_PUBLIC_`-prefixed env var. It needs to be rotated and scrubbed from that repo's git history.
Small, unglamorous secrets-hygiene mistakes like this are the same category of risk as the SQL
scoping gap — not exotic, easy to make once and forget about, and exactly the kind of thing worth a
quick pass ("does anything in this repo expose a secret client-side or commit one to git?") before
any real user's data is on the line, not just the one gap this section originally called out.

## 4. Data protection compliance (wherever your users are)

**Broadened from an earlier draft of this section, which only covered India.** The product is
global now, so "comply with one country's law" isn't the right frame — the practical checklist
below (privacy policy, retention, no-training commitment, breach plan) is closer to universal good
practice than any single regime's specific requirements, which is exactly why it's worth doing
regardless of exactly which laws end up applying to which user. Two regimes worth naming
specifically, since they cover a lot of ground:

- **India's DPDP Act, 2023** — relevant given the product's origin and likely early users. Its
  implementing Rules were notified November 14, 2025, kicking off an 18-month phased rollout: 2026
  is effectively the "build and be ready" year, with soft enforcement expected through 2026 and
  full enforcement expected around mid-May 2027. Startups/MSMEs aren't exempt, though obligations
  can be calibrated to scale and risk. Penalties for serious breaches are substantial (reported
  ranges run roughly ₹50 crore to ₹250 crore per violation) — a real business risk, not paperwork.
- **EU GDPR** — relevant the moment any EU-based business signs up, which a global, no-niche
  product should expect. GDPR is stricter than DPDP on several points (e.g. a harder requirement
  around lawful basis for processing, and a more developed "right to be forgotten"). A product that
  meets GDPR's bar tends to satisfy DPDP's too, which makes GDPR-level practice a reasonable
  default to build toward rather than picking a regime per user.

**What this means practically for this product**, given it stores personal data (customer records
inside uploaded business files, plus users' own emails/names):

- **Privacy policy and ToS** stating clearly what's collected, why, how long it's retained, and how
  a user can request deletion. This is table stakes and cheap to do now.
- **Explicit no-training commitment.** State plainly that uploaded business/customer data is not
  used to train or fine-tune any model, unless a user opts in — this is both a compliance-relevant
  purpose-limitation point and a genuine trust/marketing asset for a product asking businesses to
  upload sensitive sales data. This now needs to cover every provider in the multi-LLM cascade
  (`12-llm-orchestration.md`) — Groq, Gemini, the open-source option, and Claude all see query/data
  content at some point, and the commitment needs to hold across all of them, not just whichever one
  happens to answer a given request.
- **Data retention and deletion policy**, with an actual mechanism behind it — not just a policy
  document. `FileUpload` and `QueryLogs` records need a defined retention window and a real deletion
  path once a user requests it or closes their account.
- **Data processor awareness.** Every LLM provider in the cascade, plus Pinecone once wired, all
  process data on this product's behalf. Engaging a processor doesn't transfer the underlying
  responsibility for that data under either DPDP or GDPR — worth reviewing each provider's own data
  handling terms before they're in the critical path for real user data, not after.
- **Breach response plan.** Even a short, honest internal runbook ("who gets notified, what gets
  checked, how affected users are told") is far better than improvising one during an actual
  incident.
- None of this needs to be enterprise-grade on day one — both regimes above expect small
  operators to calibrate to their size and risk. But "we'll deal with it later" stops being a
  reasonable default once real (non-test) user data is being stored, which lines up with the same
  point the security gap in §3 draws the line at.

## 5. Acceptance criteria for this file

- [x] `PipelineOutput.confidence` is a bounded field before any UI displays it.
      *(`Field(ge=0.0, le=1.0)`, B4.)*
- [x] `/chat` (specs/05+06) ships with a visible "show the query behind this" affordance.
      *(`PipelineOutput.sql_query` + `data_preview` carry the exact SQL and raw row slice
      end-to-end, B4.)*
- [x] `QueryLogs` is actually written to on every query. *(Written per `/chat` request incl.
      graceful fallbacks, B4.)*
- [x] A basic "flag this answer" mechanism exists, feeding into `QueryLogs`. *(`POST /chat/flag`,
      own-only, sets `QueryLogs.flagged`, B4.)*
- [x] The clarifying-question pattern (§2, `specs/06` FR7) is implemented and exercised by at least
      one real pipeline path, not just a schema field that's never populated. *(The pipeline's
      SYSTEM_PROMPT ask-don't-guess path; benchmarking's first concrete use lands with B6.)*
- [x] The SQL user-scoping gap (specs/05) is closed before any non-test user's data is stored.
      *(B2: structural per-user tables + `assert_user_scoped`.)*
- [ ] The previously-exposed Tambo key (§3) is rotated and scrubbed from git history.
- [ ] A privacy policy, ToS, and data retention/deletion policy exist and are linked from the app
      before public signup opens.
