import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { ActivityPage } from './ActivityPage';

function renderActivity() {
  return render(
    <MemoryRouter>
      <ActivityPage />
    </MemoryRouter>,
  );
}

describe('ActivityPage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('shows window and lifetime quota cards', () => {
    renderActivity();
    expect(screen.getByText('Window quota')).toBeInTheDocument();
    expect(screen.getByText('Lifetime quota')).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: 'Window quota left' })).toBeInTheDocument();
    expect(
      screen.getByRole('meter', { name: 'Lifetime quota left' }),
    ).toBeInTheDocument();
  });

  it('shows empty conversations and notices states before any chat', () => {
    renderActivity();
    expect(screen.getByText(/no threads yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no quota or error notices/i)).toBeInTheDocument();
  });
});
