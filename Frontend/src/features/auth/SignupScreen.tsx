/**
 * SignupScreen (F1) — register with email/password (+ optional name), specs/01
 * FR1. Registration issues tokens immediately, so a successful submit commits a
 * session and the RequireGuest guard redirects to /app. Password is min 8 chars
 * (client + backend schema layer); confirm-password is a client-only guard.
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { getErrorMessage } from '../../lib/errors';
import { FormError } from './FormError';

const PASSWORD_MIN = 8;

export function SignupScreen() {
  const { signup } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    if (password.length < PASSWORD_MIN) {
      setError(`Password must be at least ${PASSWORD_MIN} characters.`);
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    try {
      await signup({
        email: email.trim(),
        password,
        name: name.trim() || undefined,
      });
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <h1 className="auth-form__title">Create your account</h1>

      <label className="auth-field">
        <span className="auth-field__label">Name (optional)</span>
        <input
          className="auth-field__input"
          type="text"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

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

      <label className="auth-field">
        <span className="auth-field__label">Password</span>
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
        <span className="auth-field__label">Confirm password</span>
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

      <button className="auth-form__submit" type="submit">
        Create account
      </button>

      <p className="auth-form__note">
        Already have an account? <Link to="/signin">Sign in</Link>
      </p>
    </form>
  );
}
