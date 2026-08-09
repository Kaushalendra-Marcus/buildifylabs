/**
 * getErrorMessage — the single place auth screens (and later flows) turn a
 * thrown error into a display string.
 *
 * Anti-enumeration rule (specs/01 §4, Frontend/docs/structure.md): the backend
 * deliberately returns the *same* generic message for "no such user" and "wrong
 * password". We must show that message **verbatim** and never re-word, infer,
 * or "helpfully" specialize it client-side. A string `detail` is therefore
 * passed through untouched. The only messages we author here are fallbacks for
 * non-API failures (network), which carry no enumeration risk.
 */
import { ApiError } from './http';
import type { ApiErrorBody } from '../types';

interface ValidationIssue {
  msg?: unknown;
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.body as ApiErrorBody | null;
    const detail = body?.detail;

    if (typeof detail === 'string' && detail.trim().length > 0) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          typeof item === 'object' && item !== null && 'msg' in item
            ? String((item as ValidationIssue).msg ?? '').trim()
            : '',
        )
        .filter((msg) => msg.length > 0);
      if (messages.length > 0) return messages.join('; ');
    }

    if (error.status === 422) return 'Please check your input and try again.';
  }

  return 'Something went wrong. Please try again.';
}
