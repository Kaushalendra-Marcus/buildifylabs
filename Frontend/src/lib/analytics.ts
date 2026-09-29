/**
 * Analytics (GA4) — page-view tracking for the SPA. Everything is gated on
 * `VITE_GA_MEASUREMENT_ID`: unset means analytics stays fully off (no
 * script, no network, no-op calls), so local dev and tests never phone
 * home. Set the id in Vercel env and redeploy to switch it on.
 */

declare global {
  interface Window {
    dataLayer?: Array<unknown>;
    gtag?: (...args: unknown[]) => void;
  }
}

let initialized = false;

export function getMeasurementId(): string {
  const id = import.meta.env.VITE_GA_MEASUREMENT_ID as string | undefined;
  return (id ?? '').trim();
}

/** Inject gtag.js once. Returns true when analytics is active. */
export function initAnalytics(): boolean {
  const id = getMeasurementId();
  if (!id || initialized || typeof document === 'undefined') return initialized;
  try {
    window.dataLayer = window.dataLayer ?? [];
    window.gtag = function gtag(...args: unknown[]) {
      window.dataLayer?.push(args);
    };
    window.gtag('js', new Date());
    // Manual page_view per SPA route (see trackPageview) — the automatic
    // one would double-count on client-side navigation.
    window.gtag('config', id, { send_page_view: false });
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    document.head.appendChild(script);
    initialized = true;
  } catch {
    return false;
  }
  return true;
}

/** Record an SPA page view. Safe to call before/without init (no-op). */
export function trackPageview(path: string): void {
  if (!initialized || typeof window.gtag !== 'function') return;
  try {
    window.gtag('event', 'page_view', { page_path: path });
  } catch {
    /* analytics must never break the app */
  }
}

/** Reset for tests only (re-inits on next initAnalytics call). */
export function __resetAnalyticsForTests(): void {
  initialized = false;
}
