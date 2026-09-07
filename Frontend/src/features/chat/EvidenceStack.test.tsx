/**
 * Evidence stack tests — sources / citations / process (amber, derived).
 * Covers the mockup-driven fixes: grouped history rail, expandable sources
 * with citation targets, `[n]` superscripts in prose, and the derived
 * process trace with no invented timings.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { MessageStream } from './MessageStream'
import { HistoryRail } from './HistoryRail'
import { useChatStore } from './chat-store'
import type { PipelineOutput } from '../../types/chat'

function makeOutput(overrides: Partial<PipelineOutput> = {}): PipelineOutput {
  return {
    answer: 'Gross margin held steady at 71.3% [1] after the migration [2].',
    visuals: [],
    insights: [],
    summary: '',
    root_causes: [],
    recommendations: [],
    news_context: [],
    anomalies: [],
    confidence: 0.91,
    clarification: null,
    sql_query: 'SELECT margin FROM financials JOIN costs ON costs.id = financials.id;',
    data_preview: [{ margin: 71.3, month: 'Jun' }],
    query_log_id: 'log-1',
    web_sources: [
      {
        title: 'Infrastructure cost breakdown',
        url: 'https://example.com/costs',
        provider: 'Example',
        retrieved_at: new Date('2026-07-01T10:00:00Z').toISOString(),
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  useChatStore.getState().clearChat()
  useChatStore.setState({ conversations: [], activeConversationId: null })
  useChatStore.getState().setHasData(null)
})

describe('Evidence stack — sources, citations, process', () => {
  it('renders [n] citations as superscript links to the numbered source cards', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(makeOutput())
    render(<MessageStream />)

    const cites = screen.getAllByRole('link', { name: /Source \d/ })
    expect(cites).toHaveLength(2)
    expect(cites[0]).toHaveAttribute('href', '#source-1')

    // Expanding sources reveals the numbered landing targets.
    await user.click(screen.getByRole('button', { name: /2 sources/ }))
    expect(document.getElementById('source-1')).toBeInTheDocument()
    expect(document.getElementById('source-2')).toBeInTheDocument()
  })

  it('shows the sources line with a your-data badge and derived table names', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(makeOutput())
    render(<MessageStream />)

    expect(screen.getByRole('button', { name: /2 sources/ })).toHaveTextContent('1 your data')
    expect(screen.getByRole('button', { name: /2 sources/ })).toHaveTextContent('1 live web')

    await user.click(screen.getByRole('button', { name: /2 sources/ }))
    expect(screen.getByText('financials, costs')).toBeInTheDocument()
    expect(screen.getByText('Infrastructure cost breakdown')).toBeInTheDocument()
    // Titles only: no raw URL text, no provider tag in the cards —
    // the heading itself stays the clickable link.
    expect(screen.queryByText('https://example.com/costs')).not.toBeInTheDocument()
    expect(screen.queryByText('Example')).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Infrastructure cost breakdown' }),
    ).toHaveAttribute('href', 'https://example.com/costs')
  })

  it('expands the process trace with derived steps and no invented timings', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(makeOutput())
    render(<MessageStream />)

    await user.click(screen.getByRole('button', { name: /Process · 5 steps/ }))
    expect(screen.getByRole('list', { name: 'Process steps' })).toBeInTheDocument()
    expect(screen.getByText('SQL executed')).toBeInTheDocument()
    expect(screen.getByText('1 rows · 2 cols')).toBeInTheDocument()
    // No fake millisecond timings anywhere in the trace.
    expect(screen.queryByText(/\d+ms/)).not.toBeInTheDocument()
  })

  it('shows the amber processing card (not bare dots) while thinking', () => {
    useChatStore.getState().addUserMessage('Why did revenue drop last week?')
    useChatStore.getState().setPending('thinking')
    render(<MessageStream />)

    expect(screen.getByRole('status', { name: 'Assistant is thinking' })).toBeInTheDocument()
    expect(screen.getByText('Processing')).toBeInTheDocument()
    expect(screen.getByText('running..')).toBeInTheDocument()
  })
})

describe('HistoryRail — grouped threads', () => {
  it('groups threads under Today/Yesterday with New chat and an active row', async () => {
    const user = userEvent.setup()
    const noon = new Date();
    noon.setHours(12, 0, 0, 0);
    const todayNoon = noon.getTime();
    const yesterdayNoon = todayNoon - 24 * 60 * 60 * 1000;
    useChatStore.setState({
      conversations: [
        { id: 'c-today', title: 'Revenue drop analysis', updatedAt: todayNoon },
        { id: 'c-yesterday', title: 'Gross margin breakdown', updatedAt: yesterdayNoon },
      ],
      activeConversationId: 'c-today',
    })
    render(<HistoryRail open />)

    expect(screen.getByRole('button', { name: 'New chat' })).toBeInTheDocument()
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('Yesterday')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revenue drop analysis' })).toHaveAttribute(
      'aria-current',
      'true',
    )

    await user.click(screen.getByRole('button', { name: 'Gross margin breakdown' }))
    expect(useChatStore.getState().activeConversationId).toBe('c-yesterday')
  })
})
