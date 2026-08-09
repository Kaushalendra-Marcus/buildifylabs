/** Upload contracts — mirror of docs/type-contracts.md §Upload (live backend). */

export type FileStatus = 'processing' | 'completed' | 'failed';

export interface FileResponse {
  id: string;
  file_name: string;
  file_type: string;
  file_size: number | null;
  status: FileStatus;
  pinecone_namespace: string | null;
  /** Stored ingestion failure reason (specs/04 edge case 1) — present when
   *  status === "failed". */
  error: string | null;
  created_at: string; // ISO 8601
}
