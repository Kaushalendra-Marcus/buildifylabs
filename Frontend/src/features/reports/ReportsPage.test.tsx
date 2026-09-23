import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { ReportsPage } from './ReportsPage';
import { useReportsStore } from './reports-store';
import type { PipelineOutput } from '../../types/chat';

function sampleOutput(): PipelineOutput {
  return {
    answer: 'Revenue fell 7.4% week over week.',
    visuals: [],
    insights: [],
    summary: '',
    root_causes: [],
    recommendations: [],
    news_context: [],
    anomalies: [],
    confidence: 0.7,
    clarification: null,
    sql_query: 'SELECT 1',
    data_preview: [],
    query_log_id: 'log-1',
  };
}

function renderReports() {
  return render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  );
}

describe('ReportsPage + reports-store', () => {
  beforeEach(() => {
    localStorage.clear();
    useReportsStore.getState().clearReports();
  });

  it('shows an empty state before anything is pinned', () => {
    renderReports();
    expect(screen.getByText(/nothing pinned yet/i)).toBeInTheDocument();
  });

  it('pins from the store and unpins from the page', async () => {
    const user = userEvent.setup();
    useReportsStore.getState().pinReport(sampleOutput(), 'conv-1');
    renderReports();
    expect((await screen.findAllByText(/revenue fell/i)).length).toBeGreaterThanOrEqual(1);
    await user.click(screen.getByRole('button', { name: /unpin/i }));
    expect(screen.getByText(/nothing pinned yet/i)).toBeInTheDocument();
  });
});
