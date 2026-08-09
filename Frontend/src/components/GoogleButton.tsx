/**
 * GoogleButton (F1) — Sign In With Google via Google Identity Services (GIS,
 * script in index.html, specs/01 FR4).
 *
 * The credential (an ID token) is handed to `POST /auth/google` through the
 * `useAuth` seam; any backend error propagates to the caller via `onError` so
 * the screen can show it verbatim. GIS is initialized lazily (the script loads
 * async) by polling until `google.accounts.id` appears. No `VITE_GOOGLE_CLIENT_ID`
 * ⇒ nothing renders (the button is simply absent).
 */
import { useEffect, useRef } from 'react';
import { useAuth } from '../hooks/useAuth';
import './GoogleButton.css';

type GsiWindow = Window & { google?: typeof google };

const GOOGLE_CLIENT_ID: string | undefined =
  import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

interface GoogleButtonProps {
  /** Backend auth error, shown verbatim by the calling screen. */
  onError?: (error: unknown) => void;
}

export function GoogleButton({ onError }: GoogleButtonProps) {
  const { signInWithGoogle } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    let cancelled = false;
    let rendered = false;
    let poll: number | undefined;

    const accounts = () => (window as GsiWindow).google?.accounts?.id;

    const init = () => {
      if (cancelled || rendered || !containerRef.current) return;
      const gsi = accounts();
      if (!gsi) return;

      rendered = true;
      gsi.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: (response) => {
          void signInWithGoogle({ token: response.credential }).catch((err) => {
            onErrorRef.current?.(err);
          });
        },
      });
      gsi.renderButton(containerRef.current, {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        text: 'continue_with',
        shape: 'rectangular',
        width: '100%',
      });
    };

    init();
    if (!rendered) {
      poll = window.setInterval(init, 250);
    }

    return () => {
      cancelled = true;
      if (poll !== undefined) window.clearInterval(poll);
      accounts()?.cancel?.();
    };
  }, [signInWithGoogle]);

  if (!GOOGLE_CLIENT_ID) return null;

  return <div ref={containerRef} className="google-button" />;
}
