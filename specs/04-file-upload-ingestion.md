# Spec 04 — File Upload Validation & Ingestion Pipeline

**Status:** ⚠️ Partially implemented — upload **validation** middleware is complete; storage,
parsing, cleaning, chunking, and embedding are **not implemented**.
**Source files (existing):** `app/middlewares/file_validator.py`, `app/db/models/file_upload.py`,
`app/schemas/file_upload.py`
**Source files (missing):** `app/routes/upload.py`, `app/services/data/*` (CSV/PDF parsing,
cleaning, chunking), Pinecone integration

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
  `content_type` (both must independently pass).
- **FR4:** On an accepted upload: create a `FileUpload` row (`status = "processing"`), persist the
  raw file, parse + clean it, chunk it, embed the chunks, upsert into a per-user-per-file Pinecone
  namespace, then set `status = "completed"` (or `"failed"` with a reason).
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
- Errors: `403` guest / invalid plan; `415` wrong extension or MIME type; `413` over size limit.

**`GET /files`** (auth required) → `FileResponse[]`

**`GET /files/{id}`** (auth required, must own the file) → `FileResponse`
- Errors: `404` not found or not owned by caller.

## 4. Constraints

- File type is double-checked — extension **and** MIME type must both pass. A mismatched pair
  (e.g. `.csv` extension with `application/pdf` content-type) is rejected by design.
- The size check currently happens after `await file.read()` loads the whole file into memory —
  acceptable at these caps (≤10 MB) but would need to move to streaming size checks before raising
  the caps meaningfully.
- **No storage backend is chosen or implemented yet.** Validation can pass today with nowhere for
  the file to actually be persisted afterward — this is the first thing to build for FR4.
- One user, one file at a time is the initial scope — no multi-file merge/join across uploads
  planned yet.

## 5. Edge Cases & Error Handling

1. **Upload passes validation but downstream processing fails** (bad parse, embedding API error):
   `status` must transition to `"failed"` with a stored reason, not remain stuck on `"processing"`
   indefinitely. No such failure-handling path exists yet.
2. **Malformed/corrupt CSV** (wrong encoding, ragged rows, mixed date formats): needs a defensive
   `pandas`-based cleaning pass (nulls, type coercion, dedupe) before any embedding/querying — not
   yet built.
3. **Duplicate filename from the same user:** no uniqueness constraint on `file_name`; multiple
   rows with the same name are allowed — acceptable, they're distinct uploads.
4. **Empty file (0 bytes):** passes the size check trivially today. Must be explicitly rejected
   with a clear `400`, not silently processed as an empty dataset.
5. **Spoofed `content_type`:** client-supplied and technically spoofable — the extension+MIME
   double-check raises the bar but doesn't guarantee well-formed content. The downstream parser
   must still handle malformed content defensively rather than trusting the validator fully.

## 6. Acceptance Criteria

- [ ] A guest's upload attempt always returns `403` before any file bytes are read.
- [ ] A 15 MB file from a `pro` user (over the 10 MB cap) is rejected with `413`, no partial write.
- [ ] An `.exe` renamed to `.csv` is rejected (extension check passes; content-type check catches
      it).
- [ ] A successfully processed file is queryable by the AI pipeline via its Pinecone namespace
      within an agreed target time (TBD once the pipeline is built).
- [ ] An empty (0-byte) file is explicitly rejected, not silently accepted.
