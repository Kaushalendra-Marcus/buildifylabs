/**
 * Thin fetch wrapper — the one HTTP client in the app (F0 decision: no axios).
 *
 * Responsibilities: base URL (`VITE_API_BASE_URL`), the Bearer access token
 * (from `tokenStorage`, memory-only by design), JSON encode/decode, and turning
 * non-2xx responses into a typed `ApiError` so callers can branch on
 * `status`/`body` (e.g. the two 429 quota states, specs/14 §5.6).
 *
 * Components call the domain modules in `src/api/*` — never `http` directly —
 * so swapping a mocked endpoint for the real one is a one-file change.
 */
import { tokenStorage } from './token-storage';
import type { ApiErrorBody, QuotaErrorBody } from '../types';

const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** Narrow a thrown error to the 429 quota body (specs/02 §3). */
export function isQuotaError(
  error: unknown,
): error is ApiError & { body: QuotaErrorBody } {
  return (
    error instanceof ApiError &&
    error.status === 429 &&
    typeof error.body === 'object' &&
    error.body !== null &&
    'detail' in (error.body as ApiErrorBody)
  );
}

interface RequestOptions {
  /** Body to send (JSON unless `isFormData`). */
  body?: unknown;
  /** Send the body as FormData (multipart uploads) instead of JSON. */
  isFormData?: boolean;
}

async function request<T>(
  path: string,
  method: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers();
  const token = tokenStorage.getAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  let body: BodyInit | undefined;
  if (options.isFormData) {
    body = options.body as FormData;
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(options.body);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body,
  });

  if (!response.ok) {
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // Non-JSON error body — leave null.
    }
    throw new ApiError(response.status, errorBody);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const http = {
  get: <T>(path: string): Promise<T> => request<T>(path, 'GET'),
  post: <T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> =>
    request<T>(path, 'POST', { ...options, body }),
};
