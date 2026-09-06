/**
 * AccountMenu (F2 shell) — the header account affordance (specs/14 §3). An
 * outline of the contents lives in specs/14 §9.3 (open question); the shell
 * provides a compact dropdown with the signed-in identity and sign-out.
 */
import { ChevronDown, LogOut } from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../../hooks/useAuth';

/** Initials for the header avatar circle ("Ada Lovelace" → "AL"). */
function initialsFor(label: string): string {
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();
  return initials || '•';
}

export function AccountMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);

  const label = user?.name ?? user?.email ?? 'Guest';

  return (
    <div className="account-menu">
      <button
        type="button"
        className="account-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="account-menu__avatar" aria-hidden="true">
          {initialsFor(label)}
        </span>
        <span className="account-menu__name">{label}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div
          className="account-menu__menu"
          role="menu"
          onClick={(event) => {
            if ((event.target as HTMLElement).closest('[data-sign-out]')) return;
            setOpen(false);
          }}
        >
          <span className="account-menu__identity">{label}</span>
          <button
            type="button"
            role="menuitem"
            data-sign-out
            onClick={() => {
              signOut();
              setOpen(false);
            }}
          >
            <LogOut size={14} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}