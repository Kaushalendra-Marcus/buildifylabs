/**
 * useRevealRoot — scroll-reveal for the landing page. The returned ref goes
 * on a container; every `.bl-reveal` descendant fades/slides in the first
 * time it enters the viewport, then stays visible. No-IntersectionObserver
 * environments (jsdom) reveal everything immediately so content — and tests
 * — never get stuck invisible. Pure CSS owns the animation; this only flips
 * the `is-visible` class.
 */
import { useEffect, useRef } from 'react';

export function useRevealRoot<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const targets = root.querySelectorAll('.bl-reveal');
    if (typeof IntersectionObserver === 'undefined') {
      targets.forEach((target) => target.classList.add('is-visible'));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' },
    );
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);

  return ref;
}
