/**
 * FormError — renders a server/auth error **verbatim** (anti-enumeration,
 * specs/01 §4). The message text is produced by `getErrorMessage` in
 * src/lib/errors.ts, never re-worded here.
 */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="auth-form__error" role="alert">
      {message}
    </p>
  );
}
