# Spec 04 — File Upload Validation & Ingestion Pipeline

**Status:** ⚠️ Partially implemented — validation, the `POST /files/upload` route, local-disk
storage (gap #4), and defensive **CSV** parsing → per-user data table are complete. Still missing:
PDF/XLSX parsing (deferred), chunking, and Pinecone embedding (deferred — the parsed per-user table
is queried directly).
**Source files (existing):** `app/middlewares/file_validator.py`, `app/db/models/file_upload.py`,
`app/schemas/file_upload.py`, `app/routes/files.py` (new, B3),
`app/services/data/storage.py` (new, B3), `app/services/data/parser.py` (new, B3)
**Source files (missing):** Pinecone integration (chunking + embeddings)

---

## 1. Problem Statement

Users need to upload their business data (CSV/PDF/XLSX) so the AI pipeline has something to query.
The file must be validated (plan-based size/type limits) before it's accepted, then parsed,
cleaned, and made queryable — none of the post-validation steps exist yet.

## 2. Functional Requirements

- **FR1:** Reject uploads from guest users entirely (`403`) before any file bytes are read.
- **FR2:** Enforce per-plan size caps: `free` ≤ 3 MB, `pro` ≤ 10 MB (current actual values in
  `file_validator.py` — supersedes any earlier 5 MB/50 MB figure from initial planning).
- **FR3:** Accept only `.csv`, `.pdf`, `.xlsx` — validated by **both** file extension and declared
  `content_type` (the extension's expected MIME must match; a mismatched pair is `415`).
- **FR4:** On an accepted upload: create a `FileUpload` row (`status = "processing"`), persist the
  raw file, parse + clean it, then set `status = "completed"` (or `"failed"` with a stored reason).
  *Implemented subset (B3):* CSV parses into a **per-user data table** the SQL layer queries
  directly. Chunking + embedding + Pinecone upsert are **deferred** (the per-user table is the
  queryable storage ref for now).
- **FR5:** List a user's uploaded files with their current status.

## 3. API Contracts (proposed)

**`POST /files/upload`** (auth required, non-guest, `validate_file_upload` dependency)
- Input: `multipart/form-data`, single file field.
- Output 202 (`FileResponse`):
  ```json
  {
    "id": "uuid", "file_name": "sales.csv", "file_type": "text/csv",
    "file_size": 128000, "status": "processing",
    "pinecone_namespace": null, "created_at": "iso8601"
  }
  ```
- Errors: `403` guest / invalid plan; `415` wrong extension **or** wrong/mismatched MIME type; `413`
  over size limit; `400` empty (0-byte) file.

**`GET /files`** (auth required) → `FileResponse[]`

**`GET /files/{id}`** (auth required, must own the file) → `FileResponse`
- Errors: `404` not found or not owned by caller.

## 4. Constraints

- File type is double-checked — extension **and** MIME type must both pass, enforced **per type**
  (`.csv` → `text/csv`, `.pdf` → `application/pdf`, `.xlsx` → the xlsx MIME). A mismatched pair
  (e.g. `.csv` extension with `application/pdf` content-type) is rejected with `415` by design.
- The size check currently happens after `await file.read()` loads the whole file into memory —
  acceptable at these caps (≤10 MB) but would need to move to streaming size checks before raising
  the caps meaningfully.
- **Storage backend (gap #4, resolved in B3):** **local disk** for dev (`UPLOAD_DIR` config,
  `app/services/data/storage.py`), object store (S3) for prod — storage.py is the swap seam.
- Parsed data lands in a **per-user data table** (`user_data_table_name`) in the same DB; a new
  upload **replaces** the user's data table (one user, one active data file — no multi-file
  merge/join across uploads yet). `FileUpload.pinecone_namespace` temporarily holds the per-user
  table name as the "storage ref" until Pinecone ships.

## 5. Edge Cases & Error Handling

1. **Upload passes validation but downstream processing fails** (bad parse, embedding API error):
   `status` transitions to `"failed"` with a stored reason (`FileUpload.error`), never stuck on
   `"processing"`. **Implemented** — CSV parse/ingest errors set `failed` + trimmed reason.
2. **Malformed/corrupt CSV** (wrong encoding, ragged rows, mixed date formats): a defensive
   `pandas`-based cleaning pass (encoding fallback, column normalization, nulls, type/date/currency
   coercion, row dedupe, ragged-row tolerance) runs before the table insert. **Implemented**.
3. **Duplicate filename from the same user:** no uniqueness constraint on `file_name`; multiple
   rows with the same name are allowed — acceptable, they're distinct uploads.
4. **Empty file (0 bytes):** rejected explicitly with a clear `400` in `file_validator.py`.
   **Implemented.**
5. **Spoofed `content_type`:** client-supplied and technically spoofable — the per-type
   extension+MIME double-check raises the bar, and the downstream parser still handles malformed
   content defensively rather than trusting the validator fully.

## 6. Acceptance Criteria

- [x] A guest's upload attempt always returns `403` before any file bytes are read.
- [x] A 15 MB file from a `pro` user (over the 10 MB cap) is rejected with `413`, no partial write.
- [x] An `.exe` renamed to `.csv` is rejected (extension check passes; the per-type content-type
      check catches it).
- [x] A successfully processed CSV is queryable by the AI pipeline via its **per-user data table**
      (Pinecone namespace deferred — target: TBD once the pipeline is built).
- [x] An empty (0-byte) file is explicitly rejected, not silently accepted.
