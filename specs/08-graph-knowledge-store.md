# Spec 08 — Graph Knowledge Store (Neo4j) for Hallucination-Resistant Retrieval

**Status:** ❌ Not started. No code exists.
**Source files:** none yet — extends the ingestion pipeline (spec `04`) and is consumed by the
insight pipeline's retrieval step (spec `06`).

---

## 1. Problem Statement

Pure vector similarity search (Pinecone alone) retrieves chunks that are *semantically similar* to
a query, not necessarily *factually connected* to it. Two chunks can sound related without actually
describing the same entities or relationships, and an LLM asked to reason over loosely-related
chunks is more likely to stitch together a plausible-sounding but wrong answer. A graph database
captures **explicit, exact relationships** extracted from the user's data (which product sold in
which region, which order belongs to which customer, on which date) so retrieval can be grounded
in relationships that are verifiably true in the source data, not just "sounds related." This spec
adds Neo4j as a second retrieval source alongside Pinecone, specifically to reduce hallucination
risk in the insight pipeline (spec `06`).

## 2. Functional Requirements

- **FR1 — Entity/relationship extraction at ingestion:** After a file is parsed and cleaned (spec
  `04`), extract entities (e.g. `Product`, `Region`, `Customer`, `Order`, `Date`/`Period`) and
  relationships between them (e.g. `(:Order)-[:CONTAINS]->(:Product)`,
  `(:Order)-[:PLACED_BY]->(:Customer)`, `(:Product)-[:SOLD_IN]->(:Region)`,
  `(:Order)-[:OCCURRED_ON]->(:Date)`) and write them into Neo4j.
- **FR2 — Dual write, not a replacement:** Pinecone continues to store chunk-level semantic
  embeddings (row summaries for tabular data, passage chunks for PDFs). Neo4j stores the structured
  relationships. Neither replaces the other — they answer different kinds of questions.
- **FR3 — Hybrid retrieval at query time:** For a given query, run both (a) vector similarity
  search in Pinecone and (b) a scoped Cypher query in Neo4j derived from the query's referenced
  entities. Both result sets feed the insight pipeline (spec `06`).
- **FR4 — Graph facts take precedence on conflict:** When a fact retrieved from the graph
  contradicts or is more specific than a chunk retrieved from the vector store, the pipeline prompt
  must explicitly instruct the LLM to prefer the graph fact — it represents an exact relationship,
  not an approximate semantic match.
- **FR5 — Per-user isolation:** Every node/relationship must be scoped to the owning user (and
  ideally the specific file), so one user's graph can never be traversed or returned in another
  user's query. This mirrors the SQL user-scoping requirement already flagged as a blocking gap in
  spec `05` — the same discipline applies here and is equally non-negotiable.
- **FR6 — Cleanup on file deletion:** Deleting an uploaded file must remove both its vector
  embeddings **and** its corresponding graph subgraph. A partial delete leaves stale/incorrect
  context available to future queries.

## 3. API Contracts (internal — consumed by ingestion and the pipeline, no HTTP surface)

**`build_graph_from_upload(rows_or_text, user_id: UUID, file_id: UUID) -> None`**
- Input: parsed/cleaned tabular rows (for CSV/XLSX) or extracted text (for PDF), plus the owning
  user and file IDs.
- Behavior: extracts entities + relationships, writes them into Neo4j with `user_id` (and `file_id`)
  set as a property on every node, so every later query can filter by ownership.
- Output: none (side-effecting); should raise/log clearly on partial failure rather than leaving a
  half-written subgraph silently.

**`graph_query_for_intent(user_id: UUID, entities: list[str], relationship_hint: str | None) -> list[dict]`**
- Input: the current user (mandatory scope), entities parsed out of the user's NL query (e.g.
  `["Product B", "North Region"]`), and an optional hint about what relationship is being asked
  about.
- Output: a list of structured facts (dicts), e.g.
  `[{"product": "Product B", "region": "North", "total_orders": 42}]`, ready to be dropped
  directly into the pipeline's prompt as ground-truth context.
- Must always include the `user_id` scope in the underlying Cypher `WHERE`/`MATCH` clause — never
  an unscoped graph-wide query.

## 4. Constraints

- **Hosting:** Neo4j AuraDB Free tier (roughly 200K nodes / 400K relationships on the free
  instance) is the intended target — fits MVP scale but must be watched as users and uploads grow.
  New config required: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
- **Extraction complexity differs by source type:**
  - Tabular data (CSV/XLSX): largely rule-based — column-name heuristics + foreign-key-style
    inference (e.g. a column literally named `region` or `product_id` is a strong signal).
  - Unstructured text (PDF): needs LLM-assisted extraction (a lightweight NER + relation-extraction
    prompt), which is meaningfully more complex and adds another LLM call — and its own latency and
    cost — per upload. **Recommendation: phase this.** Ship tabular-data graph extraction first
    (spec `04`'s primary use case); add PDF entity extraction once the tabular path is proven.
- **This adds real latency to ingestion** (parse → clean → chunk → embed → *also* extract entities
  → write graph). Must be weighed against the "ship fast" MVP goal from the overall product spec —
  acceptable to run graph extraction as a background/async step after the file is already marked
  `"processing"`, rather than blocking the upload response on it.

## 5. Edge Cases & Error Handling

1. **Ambiguous column-to-entity mapping.** A CSV column literally named `region` that actually
   contains free-text customer notes (not real region values) would, under naive heuristics, build
   a nonsense graph. Needs a validation step — e.g. cardinality checks (a real "region" column has
   a small number of repeated distinct values; free text doesn't) — before trusting a column as an
   entity type.
2. **Entity duplication from inconsistent casing/spelling** ("Delhi" vs "delhi" vs "New Delhi")
   → without a normalization/entity-resolution step at ingestion, the graph creates duplicate nodes
   instead of merging them, which defeats the purpose of exact-relationship grounding. This needs
   explicit handling (normalize casing/whitespace at minimum; fuzzy-match resolution as a stretch
   goal), not an afterthought.
3. **Graph/vector disagreement.** If the graph says one thing and a retrieved vector chunk says
   another (e.g. due to a stale re-upload), FR4's precedence rule must be enforced explicitly in
   the pipeline's prompt construction — not left as an implicit assumption the LLM might not
   follow consistently.
4. **Partial cleanup on file deletion.** If the delete operation removes the Pinecone vectors but
   the Neo4j write fails (or vice versa), the two stores fall out of sync. The delete operation
   should be treated as a single logical transaction with retry/reconciliation, not two
   independent best-effort calls.
5. **Large uploads exploding graph size.** A file with tens of thousands of rows, naively extracted,
   could create a very large number of nodes/relationships quickly — risking the Neo4j free-tier
   cap being hit across many users. Needs a row-count cap or sampling strategy, mirroring the same
   concern already flagged for `db_data` size in spec `06`.

## 6. Acceptance Criteria

- [ ] A tabular (CSV) upload produces a graph with de-duplicated entity nodes and correctly
      directed relationships, scoped only to the uploading user.
- [ ] A query answerable purely from graph facts (e.g. "which regions sold Product B in March")
      returns a **correct** answer using graph retrieval alone — proving the graph path is
      sufficient and accurate for structural questions, independent of vector retrieval quality.
- [ ] Deleting a file removes both its vector embeddings and its graph subgraph, verified by a
      direct check against both stores.
- [ ] A crafted query attempting to reference another user's entities returns no results — cross-
      user graph traversal is impossible even under adversarial input (mirrors the multi-tenant
      requirement in spec `05`).
- [ ] Conflicting graph vs. vector information is resolved in favor of the graph fact in the final
      pipeline output.
