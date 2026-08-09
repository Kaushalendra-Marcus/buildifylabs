/**
 * VerifyEmailScreen (F1) — the emailed link opens `/verify-email?token=…`,
 * which calls `GET /auth/verify-email?token=` (specs/01 FR6). The success or
 * error message is shown verbatim. Verification does not itself create a
 * session — after verifying, the user signs in.
 */
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { verifyEmail } from '../../api/auth';
import { getErrorMessage } from '../../lib/errors';

type VerifyStatus = 'loading' | 'success' | 'error';

export function VerifyEmailScreen() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';

  // Token comes from the URL, so the no-token (invalid link) state is derived
  // at mount rather than set inside the effect (react-hooks purity).
  const [status, setStatus] = useState<VerifyStatus>(token ? 'loading' : 'error');
  const [message, setMessage] = useState<string | null>(
    token ? null : 'This verification link is invalid or has expired.',
  );

  useEffect(() => {
    if (!token) return;

    let cancelled = false;
    verifyEmail(token)
      .then((res) => {
        if (cancelled) return;
        setStatus('success');
        setMessage(res.message);
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus('error');
        setMessage(getErrorMessage(err));
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  if (status === 'loading') {
    return (
      <div className="auth-form__status" role="status" aria-label="Verifying">
        <span className="auth-form__spinner" aria-hidden="true" />
        Verifying your email…
      </div>
    );
  }

  return (
    <div className="auth-form__status">
      <p
        className={status === 'success' ? 'auth-form__success' : 'auth-form__error'}
        role={status === 'error' ? 'alert' : undefined}
      >
        {message}
      </p>
      <p className="auth-form__note">
        <Link to="/signin">Go to sign in</Link>
      </p>
    </div>
  );
}
