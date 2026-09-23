import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OverviewPage } from './OverviewPage';
import { useAuthStore } from '../auth/auth-store';

vi.mock('../../api/files', () => ({
  listFiles: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
}));

function renderOverview() {
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

describe('OverviewPage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.getState().signOut();
    useAuthStore.getState().setSession({
      user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
    });
  });

  it('greets the user and shows the four KPI cards', async () => {
    renderOverview();
    expect(await screen.findByText(/namaste, ada/i)).toBeInTheDocument();
    expect(screen.getByText('Questions · 7 days')).toBeInTheDocument();
    expect(screen.getByText('Datasets')).toBeInTheDocument();
    expect(screen.getByText('Quota left')).toBeInTheDocument();
    expect(screen.getByText('Pinned reports')).toBeInTheDocument();
  });

  it('shows the getting-started checklist and quick asks', async () => {
    renderOverview();
    expect(await screen.findByText('Getting started')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Why did revenue drop last week?' }),
    ).toBeInTheDocument();
  });

  it('shows an empty recent-insights state before the first answer', async () => {
    renderOverview();
    expect(await screen.findByText(/no answers yet/i)).toBeInTheDocument();
  });
});
