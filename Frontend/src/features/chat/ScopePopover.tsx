/**
 * ScopePopover — the mobile-compact source + quota control. The desktop
 * composer shows the 3-way scope segments and the quota chip inline; on
 * phones that whole row collapses into one icon button (see Composer) and
 * this popover carries the same choices: scope radio options plus the
 * rolling-window quota line. Selecting a scope applies it and closes.
 */
import { Database, Globe, Layers } from 'lucide-react';
import { useNow } from '../../hooks/useNow';
import { useQuota } from '../../hooks/useQuota';
import { formatRemaining } from '../../lib/format';
import type { SourceScope } from '../../types/chat';
import { WINDOW_QUESTIONS_LIMIT } from './quota-store';
import { useScopeStore } from './scope-store';

const OPTIONS: Array<{ value: SourceScope; label: string; hint: string; Icon: typeof Database }> = [
  { value: 'own_data', label: 'Your data', hint: 'Answers from your uploaded data.', Icon: Database },
  { value: 'live_web', label: 'Live web', hint: 'Answers from live web search.', Icon: Globe },
  { value: 'both', label: 'Both', hint: 'Your data plus live web search.', Icon: Layers },
];

export function ScopePopover({ onClose }: { onClose: () => void }) {
  const scope = useScopeStore((state) => state.scope);
  const setScope = useScopeStore((state) => state.setScope);
  const { leftInWindow, resetsAt } = useQuota();
  const now = useNow(30_000);

  return (
    <div className="scope-popover" role="dialog" aria-label="Answer source and quota">
      <p className="scope-popover__title">Answer from</p>
      <div className="scope-popover__options" role="group" aria-label="Source scope">
        {OPTIONS.map(({ value, label, hint, Icon }) => (
          <button
            key={value}
            type="button"
            className="scope-popover__option"
            aria-pressed={scope === value}
            onClick={() => {
              setScope(value);
              onClose();
            }}
          >
            <Icon size={16} aria-hidden="true" />
            <span className="scope-popover__option-text">
              <span className="scope-popover__option-label">{label}</span>
              <span className="scope-popover__option-hint">{hint}</span>
            </span>
          </button>
        ))}
      </div>
      <p className="scope-popover__quota">
        {leftInWindow} of {WINDOW_QUESTIONS_LIMIT} left
        {resetsAt !== null && now !== null && (
          <>
            {' · '}
            resets in {formatRemaining(resetsAt - now)}
          </>
        )}
      </p>
    </div>
  );
}
