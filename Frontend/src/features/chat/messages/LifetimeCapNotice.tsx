/**
 * LifetimeCapNotice (F5, specs/14 §5.6 / specs/02 FR5) — the SECOND distinct
 * 429 state: the lifetime cap is a *ceiling*, not a rate limit, so it renders
 * as a more permanent-feeling CARD (never a toast) with the contact form
 * (name/email/message → POST /contact) inline.
 */
import { Gauge } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { sendContact } from '../../../api/contact';
import { getErrorMessage } from '../../../lib/errors';

export function LifetimeCapNotice() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === 'sending' || state === 'sent') return;
    setState('sending');
    setError(null);
    try {
      await sendContact({ name, email, message });
      setState('sent');
    } catch (caught) {
      setState('error');
      setError(getErrorMessage(caught));
    }
  }

  return (
    <div className="lifetime-cap-notice" role="region" aria-label="Limit reached">
      <div className="lifetime-cap-notice__heading">
        <Gauge size={16} aria-hidden="true" />
        <span>You've reached the 100-question limit for now</span>
      </div>
      <p className="lifetime-cap-notice__blurb">
        You've used your free lifetime allowance. Tell us what you need — we
        won't leave you hanging.
      </p>

      {state === 'sent' ? (
        <p className="lifetime-cap-notice__sent" role="status">
          Thanks — we'll be in touch.
        </p>
      ) : (
        <form className="lifetime-cap-notice__form" onSubmit={handleSubmit}>
          <label>
            Name
            <input
              name="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </label>
          <label>
            Email
            <input
              name="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Message
            <textarea
              name="message"
              rows={3}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={state === 'sending'}>
            {state === 'sending' ? 'Sending…' : 'Tell us'}
          </button>
          {state === 'error' && error && (
            <p className="lifetime-cap-notice__error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}