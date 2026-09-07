/**
 * AccountMenu (F2 shell) — the account affordance, docked in the history-rail
 * footer: an initials avatar + name trigger opening a dropdown with the
 * signed-in identity (name + email, or Guest + plan) and four items — Plan &
 * billing, Data sources, Contact us (each opens a small dialog on a live
 * seam), and Sign out in danger red. Amber accent only, never purple.
 *
 * `align="up"` opens the menu above the trigger (rail footer); the dialogs
 * portal to `document.body` so no overflow-clipped ancestor can trap them.
 * Closes on outside click, on Escape, or after picking an item.
 */
import { ChevronDown, CreditCard, Database, LogOut, Mail } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '../../hooks/useAuth';
import { AccountDialog, type AccountDialogKind } from './AccountDialog';

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

export function AccountMenu({ align = 'down' }: { align?: 'down' | 'up' }) {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<AccountDialogKind | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const isGuest = user?.plan === 'guest' || user === null;
  const displayName = user?.name?.trim() || user?.email?.trim() || 'Guest';
  const displayEmail = user?.email?.trim() ?? null;

  // Close on outside click / Escape — the menu is a lightweight popover.
  useEffect(() => {
    if (!open) return;
    function handlePointer(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('pointerdown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('pointerdown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open ]);

  function openDialog(kind: AccountDialogKind) {
    setOpen(false);
    setDialog(kind);
  }

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        type="button"
        className="account-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="account-menu__avatar" aria-hidden="true">
          {initialsFor(displayName)}
        </span>
        <span className="account-menu__name">{displayName}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div
          className={`account-menu__menu${align === 'up' ? ' account-menu__menu--up' : ''}`}
          role="menu"
        >
          <div className="account-menu__identity">
            <span className="account-menu__identity-name">{displayName}</span>
            {displayEmail !== null && !isGuest ? (
              <span className="account-menu__identity-email">{displayEmail}</span>
            ) : (
              <span className="account-menu__identity-email">
                {user?.plan ?? 'guest'} plan
              </span>
            )}
          </div>

          <div className="account-menu__divider" aria-hidden="true" />

          <button type="button" role="menuitem" onClick={() => openDialog('plan')}>
            <CreditCard size={15} aria-hidden="true" />
            Plan &amp; billing
          </button>
          <button type="button" role="menuitem" onClick={() => openDialog('sources')}>
            <Database size={15} aria-hidden="true" />
            Data sources
          </button>
          <button type="button" role="menuitem" onClick={() => openDialog('contact')}>
            <Mail size={15} aria-hidden="true" />
            Contact us
          </button>

          <div className="account-menu__divider" aria-hidden="true" />

          <button
            type="button"
            role="menuitem"
            className="account-menu__item--danger"
            onClick={() => {
              signOut();
              setOpen(false);
            }}
          >
            <LogOut size={15} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}

      {dialog !== null &&
        createPortal(
          <AccountDialog kind={dialog} onClose={() => setDialog(null)} />,
          document.body,
        )}
    </div>
  );
}
