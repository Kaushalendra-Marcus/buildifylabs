import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { VisualOutput } from '../../../types/chat';
import { VisualCardsGrid } from './VisualCardsGrid';

const visuals: VisualOutput[] = [
  {
    visual_type: 'metric',
    title: 'Revenue',
    subtitle: 'Revenue grew 12% versus last month.',
    props: { label: 'Revenue', value: 4.2, change_pct: 12, direction: 'up' },
  },
  {
    visual_type: 'insight',
    title: 'What changed',
    subtitle: 'Growth was concentrated in the enterprise segment.',
    props: {
      text: 'Enterprise drove most of the increase.',
      context: 'Compared with the prior month.',
    },
  },
];

describe('VisualCardsGrid — InsightDeck card layer', () => {
  it('renders a title and insight subtitle for every visual', () => {
    render(<VisualCardsGrid visuals={visuals} />);

    expect(screen.getByLabelText('Visual results')).toHaveClass(
      'visual-cards-grid',
    );
    expect(
      document.querySelectorAll('.visual-cards-grid__title'),
    ).toHaveLength(2);
    expect(
      screen.getByText('Revenue grew 12% versus last month.'),
    ).toHaveClass('visual-cards-grid__subtitle');
    expect(
      screen.getByText('Growth was concentrated in the enterprise segment.'),
    ).toHaveClass('visual-cards-grid__subtitle');
  });

  it('opens a keyboard-navigable presentation deck and restores focus on exit', async () => {
    const user = userEvent.setup();
    render(<VisualCardsGrid visuals={visuals} />);

    const present = screen.getByRole('button', { name: 'Present 2 visuals' });
    await user.click(present);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Revenue' })).toBeInTheDocument();
    expect(
      within(dialog).getByText('Revenue grew 12% versus last month.'),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('1 / 2')).toBeInTheDocument();

    await user.keyboard('{ArrowRight}');
    expect(
      within(dialog).getByRole('heading', { name: 'What changed' }),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('2 / 2')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(present).toHaveFocus();
  });
});
