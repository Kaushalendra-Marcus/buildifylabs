/**
 * SigninScreen (F1) — email/password sign-in (specs/01 FR2), plus Google (GIS)
 * and guest (`device_id`) entry points. Successful auth commits a session in
 * the store; the RequireGuest route guard then redirects to /app. Backend
 * errors (deliberately generic, anti-enumeration) are shown verbatim.
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { GoogleButton } from '../../components/GoogleButton';
import { useAuth } from '../../hooks/useAuth';
import { getErrorMessage } from '../../lib/errors';
import { getOrCreateDeviceId } from './device-id';
import { FormError } from './FormError';

export function SigninScreen() {
  const { signin, signInAsGuest } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    try {
      await signin({ email: email.trim(), password });
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  const handleGuest = async () => {
    setError(null);
    try {
      await signInAsGuest({ device_id: getOrCreateDeviceId() });
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <h1 className="auth-form__title">Sign in</h1>

      <GoogleButton onError={(err) => setError(getErrorMessage(err))} />

      <div className="auth-form__divider">
        <span>or use your email</span>
      </div>

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
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>

      <FormError message={error} />

      <button className="auth-form__submit" type="submit">
        Sign in
      </button>

      <div className="auth-form__links">
        <Link to="/forgot-password">Forgot password?</Link>
        <Link to="/signup">Create an account</Link>
      </div>

      <button className="auth-form__guest" type="button" onClick={handleGuest}>
        Continue as guest
      </button>
    </form>
  );
}
