/**
 * EmptyThread (F6, specs/14 §6) — the states shown when the thread has no
 * messages yet:
 *   - guest: invite a question; NO upload affordance at all (§5.3 — the
 *     composer's upload button is already absent for guests)
 *   - registered + no completed files: invite an upload ("Add a CSV, PDF, or
 *     spreadsheet to get started"), the UploadPopover one tap away
 *   - registered + has files: invite a question (same as guest)
 *
 * Also reports whether the user has any uploaded data into the chat store
 * (`hasData`), which the stream uses to route a no-data question through the
 * no-data messaging (07 edge case 2) instead of a generic empty.
 */
import { FileUp, Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { listFiles } from '../../../api/files';
import { useAuth } from '../../../hooks/useAuth';
import { useChatStore } from '../chat-store';
import { UploadPopover } from '../UploadPopover';

export function EmptyThread() {
  const { user } = useAuth();
  const isGuest = user?.plan === 'guest';
  const hasData = useChatStore((state) => state.hasData);
  const [checking, setChecking] = useState(true);
  const [popoverOpen, setPopoverOpen] = useState(false);

  useEffect(() => {
    if (isGuest) {
      // Guests can never upload (specs/04 FR1) — they have no data, full stop.
      // isGuest short-circuits the render before `checking` is ever consulted.
      useChatStore.getState().setHasData(false);
      return;
    }
    let cancelled = false;
    listFiles()
      .then((files) => {
        if (cancelled) return;
        useChatStore
          .getState()
          .setHasData(files.some((file) => file.status === 'completed'));
      })
      .catch(() => {
        // Leave hasData unknown — the upload popover sets it on the first
        // successful upload; the fallback keeps the generic notice until then.
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isGuest]);

  const questionInvite = (
    <div className="empty-thread">
      <span className="empty-thread__icon">
        <Sparkles size={22} aria-hidden="true" />
      </span>
      <p className="empty-thread__title">Ask anything about your business data</p>
      <p className="empty-thread__body">
        Ask a question in plain English and get the right chart, the query
        behind it, and possible factors — all traceable.
      </p>
    </div>
  );

  // Guests, and registered users with data, invite a question directly.
  if (isGuest || hasData === true) {
    return questionInvite;
  }

  // Registered user, no data (or still checking): invite an upload first.
  return (
    <div className="empty-thread">
      {checking && hasData === null ? (
        <span className="empty-thread__loading" role="status">
          Loading your workspace…
        </span>
      ) : (
        <>
          <span className="empty-thread__icon">
            <FileUp size={22} aria-hidden="true" />
          </span>
          <p className="empty-thread__title">
            Add a CSV, PDF, or spreadsheet to get started
          </p>
          <p className="empty-thread__body">
            Upload your business data, then ask questions about it in plain
            English.
          </p>
          <button
            type="button"
            className="empty-thread__action"
            onClick={() => setPopoverOpen(true)}
          >
            Add a file
          </button>
          {popoverOpen && (
            <div className="empty-thread__popover">
              <UploadPopover onClose={() => setPopoverOpen(false)} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
