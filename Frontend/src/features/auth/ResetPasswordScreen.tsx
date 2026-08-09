/**
 * ResetPasswordScreen (F1) — specs/01 FR8: signed link carries `?token=`, the
 * user sets a new password (min 8 chars, client + backend schema layer). The
 * backend's success/error message is shown verbatim.
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { resetPassword } from '../../api/auth';
import { getErrorMessage } from '../../lib/errors';
import { FormError } from './FormError';

const PASSWORD_MIN = 8;

export function ResetPasswordScreen() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setMessage(null);

    if (!token) {
      setError('This reset link is invalid or has expired.');
      return;
    }
    if (password.length < PASSWORD_MIN) {
      setError(`Password must be at least ${PASSWORD_MIN} characters.`);
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    setSubmitting(true);
    try {
      const res = await resetPassword({ token, new_password: password });
      setMessage(res.message);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <h1 className="auth-form__title">Reset password</h1>

      <label className="auth-field">
        <span className="auth-field__label">New password</span>
        <input
          className="auth-field__input"
          type="password"
          autoComplete="new-password"
          minLength={PASSWORD_MIN}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>

      <label className="auth-field">
        <span className="auth-field__label">Confirm new password</span>
        <input
          className="auth-field__input"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
      </label>

      <FormError message={error} />

      {message && <p className="auth-form__success">{message}</p>}

      <button className="auth-form__submit" type="submit" disabled={submitting}>
        Reset password
      </button>

      <p className="auth-form__note">
        <Link to="/signin">Back to sign in</Link>
      </p>
    </form>
  );
}
