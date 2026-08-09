/**
 * ForgotPasswordScreen (F1) — specs/01 FR7. The backend always returns the same
 * generic message whether or not the email is registered (anti-enumeration); we
 * show it **verbatim** and never hint at whether the account exists.
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { forgotPassword } from '../../api/auth';
import { getErrorMessage } from '../../lib/errors';
import { FormError } from './FormError';

export function ForgotPasswordScreen() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const res = await forgotPassword({ email: email.trim() });
      setMessage(res.message);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <h1 className="auth-form__title">Forgot password</h1>

      <p className="auth-form__note">
        Enter your email and we will send you a reset link if an account exists.
      </p>

      <label className="auth-field">
        <span className="auth-field__label">Email</span>
        <input
          className="auth-field__input"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </label>

      <FormError message={error} />

      {message && <p className="auth-form__success">{message}</p>}

      <button className="auth-form__submit" type="submit" disabled={submitting}>
        Send reset link
      </button>

      <p className="auth-form__note">
        <Link to="/signin">Back to sign in</Link>
      </p>
    </form>
  );
}
