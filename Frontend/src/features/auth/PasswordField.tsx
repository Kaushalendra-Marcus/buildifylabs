/**
 * PasswordField — a labelled password input with a show/hide ("eye") toggle.
 *
 * Used by every auth screen (signin / signup / reset-password) so passwords —
 * including confirm-password fields — can be visually verified before submit.
 * The wrapping `<label>` keeps `getByLabelText` association intact; the toggle
 * is `type="button"` so it never submits the form.
 */
import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';

interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  minLength?: number;
  required?: boolean;
}

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  minLength,
  required,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);

  return (
    <label className="auth-field">
      <span className="auth-field__label">{label}</span>
      <span className="auth-field__password-wrap">
        <input
          className="auth-field__input auth-field__input--with-toggle"
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          minLength={minLength}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required={required}
        />
        <button
          type="button"
          className="auth-field__password-toggle"
          aria-label={visible ? `Hide ${label}` : `Show ${label}`}
          aria-pressed={visible}
          title={visible ? 'Hide password' : 'Show password'}
          onClick={() => setVisible((show) => !show)}
        >
          {visible ? (
            <EyeOff size={17} aria-hidden="true" />
          ) : (
            <Eye size={17} aria-hidden="true" />
          )}
        </button>
      </span>
    </label>
  );
}
