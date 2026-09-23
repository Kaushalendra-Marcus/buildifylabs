import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CommandPalette } from './CommandPalette';

function renderPalette(onClose: () => void) {
  return render(
    <MemoryRouter>
      <CommandPalette open onClose={onClose} />
    </MemoryRouter>,
  );
}

describe('CommandPalette', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('renders nothing when closed', () => {
    render(
      <MemoryRouter>
        <CommandPalette open={false} onClose={() => undefined} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('dialog', { name: 'Quick switcher' })).toBeNull();
  });

  it('filters actions by query text', async () => {
    const user = userEvent.setup();
    renderPalette(() => undefined);
    expect(screen.getByText('Overview dashboard')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Search actions'), 'report');
    expect(screen.queryByText('Overview dashboard')).toBeNull();
    expect(screen.getByText('Pinned reports')).toBeInTheDocument();
  });

  it('runs the first match on Enter and closes', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderPalette(onClose);
    await user.type(screen.getByLabelText('Search actions'), 'activity');
    await user.keyboard('{Enter}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderPalette(onClose);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('stages a quick-ask for the chat composer', async () => {
    const user = userEvent.setup();
    renderPalette(() => undefined);
    await user.click(screen.getByText('Why did revenue drop last week?'));
    expect(sessionStorage.getItem('bl-pending-question')).toBe(
      'Why did revenue drop last week?',
    );
  });
});
