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

/**
 * Unary requests must not hang forever: without a timeout a stalled backend
 * leaves buttons spinning and the user with no way to retry. Streaming
 * (`sendQuery`) deliberately has no such cap because an answer can take
 * minutes; only the connection setup is bounded by the browser.
 */
const REQUEST_TIMEOUT_MS = 30_000;

/** Base URL + bearer headers, shared by the JSON client and the SSE reader. */
export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

export function authHeaders(): Headers {
  const headers = new Headers();
  const token = tokenStorage.getAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

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
  /** Per-request override for slow operations such as file uploads. */
  timeoutMs?: number;
}

async function request<T>(
  path: string,
  method: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = authHeaders();

  let body: BodyInit | undefined;
  if (options.isFormData) {
    body = options.body as FormData;
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(options.body);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? REQUEST_TIMEOUT_MS,
  );

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('The request timed out. Please try again.');
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }

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
  del: <T>(path: string): Promise<T> => request<T>(path, 'DELETE'),
};
