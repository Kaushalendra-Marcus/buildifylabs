/**
 * PresentationDeck — a read-only, full-screen view of an answer's visuals.
 *
 * This is the InsightDeck "presentation mode": it strips the app shell,
 * header, history rail, and composer; enlarges the chart canvas; keeps the
 * title, insight subtitle, and source context; and navigates with the
 * keyboard. It is deliberately NOT a resizable workspace panel — it is a
 * one-way presenter over a finished answer.
 */
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import type { VisualOutput } from '../../../types/chat';
import { VisualCard } from '../../../components/visuals/VisualCard';
import {
  visualSubtitle,
  visualTypeLabel,
} from '../../../components/visuals/visual-meta';
import './presentation.css';

interface PresentationDeckProps {
  visuals: VisualOutput[];
  deckTitle?: string;
  onClose: () => void;
}

export function PresentationDeck({
  visuals,
  deckTitle = 'Visual analysis',
  onClose,
}: PresentationDeckProps) {
  const [index, setIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const total = visuals.length;
  const safeIndex = total > 0 ? Math.min(index, total - 1) : 0;
  const current = visuals[safeIndex];

  const goTo = useCallback(
    (nextIndex: number) => {
      setIndex(Math.max(0, Math.min(total - 1, nextIndex)));
    },
    [total],
  );

  const goNext = useCallback(() => {
    setIndex((currentIndex) => Math.min(total - 1, currentIndex + 1));
  }, [total]);

  const goPrev = useCallback(() => {
    setIndex((currentIndex) => Math.max(0, currentIndex - 1));
  }, []);

  // Capture the trigger, focus the exit control, and restore focus on close.
  useEffect(() => {
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    closeRef.current?.focus();
    return () => {
      restoreFocusRef.current?.focus();
    };
  }, []);

  // Presentation is a modal: lock background scrolling while it is open.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // Keyboard contract: arrows/Home/End navigate, Escape exits.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === 'ArrowRight' || event.key === 'PageDown') {
        event.preventDefault();
        goNext();
        return;
      }
      if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
        event.preventDefault();
        goPrev();
        return;
      }
      if (event.key === 'Home') {
        event.preventDefault();
        goTo(0);
        return;
      }
      if (event.key === 'End') {
        event.preventDefault();
        goTo(total - 1);
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [goNext, goPrev, goTo, onClose, total]);

  // Keep Tab inside the dialog while it is open.
  function trapFocus(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Tab') return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (!current) return null;

  const subtitle = visualSubtitle(current);
  const isFirst = safeIndex === 0;
  const isLast = safeIndex === total - 1;

  return createPortal(
    <div
      ref={dialogRef}
      className="presentation"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onKeyDown={trapFocus}
    >
      <header className="presentation__bar">
        <div className="presentation__identity">
          <img
            src="/logo.png"
            alt=""
            className="presentation__logo"
            aria-hidden="true"
          />
          <div>
            <p className="presentation__brand">BuildifyLabs</p>
            <p className="presentation__deck-title">{deckTitle}</p>
          </div>
        </div>
        <div className="presentation__controls">
          <p className="presentation__counter">
            {safeIndex + 1} / {total}
          </p>
          <button
            ref={closeRef}
            type="button"
            className="presentation__close"
            onClick={onClose}
            aria-label="Exit presentation"
          >
            <X size={19} aria-hidden="true" />
          </button>
        </div>
      </header>

      <main className="presentation__stage">
        <figure className="presentation__slide" key={safeIndex}>
          <figcaption className="presentation__slide-head">
            <p className="presentation__kicker">
              {visualTypeLabel(current.visual_type)}
            </p>
            <h2 id={titleId} className="presentation__title">
              {current.title}
            </h2>
            {subtitle && (
              <p className="presentation__subtitle">{subtitle}</p>
            )}
          </figcaption>
          <div className="presentation__visual">
            <VisualCard visual={current} presentation />
          </div>
        </figure>

        {total > 1 && (
          <>
            <button
              type="button"
              className="presentation__nav presentation__nav--prev"
              onClick={goPrev}
              disabled={isFirst}
              aria-label="Previous visual"
            >
              <ChevronLeft size={22} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="presentation__nav presentation__nav--next"
              onClick={goNext}
              disabled={isLast}
              aria-label="Next visual"
            >
              <ChevronRight size={22} aria-hidden="true" />
            </button>
          </>
        )}
      </main>

      <footer className="presentation__footer">
        {total > 1 && (
          <div className="presentation__dots" role="group" aria-label="Visual slides">
            {visuals.map((visual, visualIndex) => (
              <button
                key={`${visual.visual_type}-${visualIndex}`}
                type="button"
                className={[
                  'presentation__dot',
                  visualIndex === safeIndex ? 'presentation__dot--active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={() => goTo(visualIndex)}
                aria-label={`Go to visual ${visualIndex + 1}: ${visual.title}`}
                aria-current={visualIndex === safeIndex ? 'true' : undefined}
              />
            ))}
          </div>
        )}
        <p className="presentation__hint">
          {total > 1 ? 'Use ← → to navigate · Esc to exit' : 'Esc to exit'}
        </p>
      </footer>

      <p className="presentation__live" aria-live="polite">
        Visual {safeIndex + 1} of {total}: {current.title}
      </p>
    </div>,
    document.body,
  );
}
