/** Shared API/error shapes. */

/** 429 quota body (specs/02 §3) — `contact_form: true` marks the lifetime cap,
 *  the two distinct 429 UI states (specs/14 §5.6) branch on this flag. */
export interface QuotaErrorBody {
  detail: string;
  contact_form?: boolean;
}

/** Generic FastAPI error body (HTTPException detail). */
export interface ApiErrorBody {
  detail?: string;
  [key: string]: unknown;
}
