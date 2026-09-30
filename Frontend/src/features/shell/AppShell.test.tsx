/**
 * AppShell mobile chat drawer tests — on the chat route with a narrow
 * viewport the topbar + tab bar step aside (CSS) and the sidebar becomes
 * a left overlay drawer with a backdrop; tapping the backdrop closes it.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../auth/auth-store'
import { AppShell } from './AppShell'
import { useShellStore } from './shell-store'

vi.mock('../../api/files', () => ({
  listFiles: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
}))

let originalMatchMedia: typeof window.matchMedia | undefined;
let hadMatchMedia = false;

function mockNarrow(narrow: boolean) {
  if (!hadMatchMedia && typeof window.matchMedia === 'function') {
    originalMatchMedia = window.matchMedia;
    hadMatchMedia = true;
  }
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(max-width: 767.98px)' ? narrow : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
}

function restoreMatchMedia() {
  if (hadMatchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    });
    hadMatchMedia = false;
  } else {
    // @ts-expect-error jsdom has no matchMedia by default — restore that.
    delete window.matchMedia;
  }
}

function renderShellAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/*" element={<AppShell />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  restoreMatchMedia();
  useAuthStore.getState().setSession({
    user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
  });
  useShellStore.getState().setNavOpen(true);
  vi.clearAllMocks();
});

describe('AppShell mobile chat drawer', () => {
  it('shows a backdrop on the chat route that closes the drawer', async () => {
    mockNarrow(true);
    renderShellAt('/app/chat');
    const user = userEvent.setup();

    expect(
      screen.getByRole('button', { name: 'Close navigation' }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Close navigation' }));
    expect(useShellStore.getState().navOpen).toBe(false);
    expect(
      screen.queryByRole('button', { name: 'Close navigation' }),
    ).not.toBeInTheDocument();
  });

  it('shows no drawer backdrop off the chat route', () => {
    mockNarrow(true);
    renderShellAt('/app');
    expect(
      screen.queryByRole('button', { name: 'Close navigation' }),
    ).not.toBeInTheDocument();
  });

  it('shows no drawer backdrop on wide screens', () => {
    mockNarrow(false);
    renderShellAt('/app/chat');
    expect(
      screen.queryByRole('button', { name: 'Close navigation' }),
    ).not.toBeInTheDocument();
  });
});
