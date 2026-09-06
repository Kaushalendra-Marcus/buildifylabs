/**
 * AccountDialog — small modal panels opened from the header account menu:
 *   - `plan`: current plan + real quota usage (quota store) + upload cap
 *     (3MB free / 10MB pro, specs/04 FR2). Upgrade stays disabled with an
 *     honest note — checkout isn't live (payments seam is mocked).
 *   - `sources`: the user's uploaded files (`GET /files`, live) with status
 *     chips; guests get a sign-in note (uploads 403 for guests, specs/04).
 *   - `contact`: name/email/message → `POST /contact` (live), same
 *     sent/error handling as the lifetime-cap form.
 *
 * Amber accent only — no purple. Closes on backdrop click or Escape.
 */
import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { sendContact } from '../../api/contact';
import { listFiles } from '../../api/files';
import { PlanBadge } from '../../components/PlanBadge';
import { useAuth } from '../../hooks/useAuth';
import { useQuota } from '../../hooks/useQuota';
import { useNow } from '../../hooks/useNow';
import { getErrorMessage } from '../../lib/errors';
import { formatRemaining } from '../../lib/format';
import { WINDOW_QUESTIONS_LIMIT, LIFETIME_QUESTIONS_LIMIT } from './quota-store';
import type { FileResponse } from '../../types';

export type AccountDialogKind = 'plan' | 'sources' | 'contact';

const DIALOG_TITLES: Record<AccountDialogKind, string> = {
  plan: 'Plan & billing',
  sources: 'Data sources',
  contact: 'Contact us',
};

function PlanPanel() {
  const { user } = useAuth();
  const { leftInWindow, lifetimeLeft, resetsAt } = useQuota();
  const now = useNow();
  const plan = user?.plan ?? 'guest';
  const uploadCap = plan === 'pro' ? '10 MB' : plan === 'free' ? '3 MB' : null;

  return (
    <div className="account-dialog__body">
      <div className="account-dialog__row">
        <span className="account-dialog__label">Current plan</span>
        <PlanBadge plan={plan} />
      </div>
      <div className="account-dialog__row">
        <span className="account-dialog__label">Questions left</span>
        <span className="account-dialog__value">
          {leftInWindow} of {WINDOW_QUESTIONS_LIMIT} this window · {lifetimeLeft} of{' '}
          {LIFETIME_QUESTIONS_LIMIT} lifetime
          {resetsAt !== null && now !== null && (
            <> · resets in {formatRemaining(resetsAt - now)}</>
          )}
        </span>
      </div>
      <div className="account-dialog__row">
        <span className="account-dialog__label">Uploads</span>
        <span className="account-dialog__value">
          {uploadCap === null
            ? 'Guests can’t upload — sign in with a free account to add CSV, PDF, or XLSX files.'
            : `Up to ${uploadCap} per file · CSV, PDF, or XLSX.`}
        </span>
      </div>
      <p className="account-dialog__note">
        Pro checkout isn’t live yet, so plans can’t change from here.
      </p>
      <button
        type="button"
        className="account-dialog__primary"
        disabled
        title="Checkout isn’t live yet"
      >
        Upgrade to Pro
      </button>
    </div>
  );
}

const FILE_STATUS_LABEL: Record<FileResponse['status'], string> = {
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
};

function SourcesPanel() {
  const { user } = useAuth();
  const [files, setFiles] = useState<FileResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user?.plan === 'guest') return;
    let cancelled = false;
    void listFiles()
      .then((listed) => {
        if (!cancelled) setFiles(listed);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(getErrorMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [user?.plan]);

  if (user?.plan === 'guest') {
    return (
      <div className="account-dialog__body">
        <p className="account-dialog__note">
          Guests can’t connect data. Sign in with a free account, then use the
          upload button in the composer to add a CSV, PDF, or spreadsheet.
        </p>
      </div>
    );
  }

  return (
    <div className="account-dialog__body">
      {error && (
        <p className="account-dialog__error" role="alert">
          {error}
        </p>
      )}
      {files === null && error === null && (
        <p className="account-dialog__note">Loading your files…</p>
      )}
      {files !== null && files.length === 0 && (
        <p className="account-dialog__note">
          No files yet. Use the upload button in the composer to add a CSV,
          PDF, or spreadsheet.
        </p>
      )}
      {files !== null && files.length > 0 && (
        <ul className="account-dialog__file-list">
          {files.map((file) => (
            <li key={file.id} className="account-dialog__file">
              <span className="account-dialog__file-name">{file.file_name}</span>
              <span
                className={`account-dialog__chip account-dialog__chip--${file.status}`}
              >
                {FILE_STATUS_LABEL[file.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ContactPanel() {
  const { user } = useAuth();
  const [name, setName] = useState(user?.name ?? '');
  const [email, setEmail] = useState(user?.email ?? '');
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

  if (state === 'sent') {
    return (
      <div className="account-dialog__body">
        <p className="account-dialog__sent" role="status">
          Thanks — we’ll be in touch.
        </p>
      </div>
    );
  }

  return (
    <div className="account-dialog__body">
      <form className="account-dialog__form" onSubmit={handleSubmit}>
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
        <button
          type="submit"
          className="account-dialog__primary"
          disabled={state === 'sending'}
        >
          {state === 'sending' ? 'Sending…' : 'Send message'}
        </button>
        {state === 'error' && error && (
          <p className="account-dialog__error" role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}

export function AccountDialog({
  kind,
  onClose,
}: {
  kind: AccountDialogKind;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKey);
    panelRef.current
      ?.querySelector<HTMLElement>('button, input, textarea')
      ?.focus();
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className="account-dialog-layer">
      <button
        type="button"
        className="account-dialog-backdrop"
        aria-label="Close dialog"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        className="account-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={DIALOG_TITLES[kind]}
      >
        <div className="account-dialog__header">
          <h2 className="account-dialog__title">{DIALOG_TITLES[kind]}</h2>
          <button
            type="button"
            className="account-dialog__close"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        {kind === 'plan' && <PlanPanel />}
        {kind === 'sources' && <SourcesPanel />}
        {kind === 'contact' && <ContactPanel />}
      </div>
    </div>
  );
}
