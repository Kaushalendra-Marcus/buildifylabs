/**
 * Message stream (F3) component tests — specs/14 §4 acceptance: the four
 * message types render (user bubble + file chip above; answer with visual
 * grid / insights strip / trust footer / news row; clarification quick-pick;
 * fallback neutral notice), hedged "Possible factors" label, and the trust
 * footer sits on every non-fallback/non-clarification answer.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MessageStream } from './MessageStream'
import { useChatStore } from './chat-store'
import type { PipelineOutput } from '../../types/chat'
import { flagAnswer } from '../../api/chat'
import { sendContact } from '../../api/contact'

vi.mock('../../api/chat', () => ({
  sendQuery: vi.fn(),
  flagAnswer: vi.fn(),
}))

vi.mock('../../api/contact', () => ({
  sendContact: vi.fn(),
}))

function makeOutput(overrides: Partial<PipelineOutput> = {}): PipelineOutput {
  return {
    answer: 'Total revenue was 4.2M this quarter.',
    visuals: [],
    insights: [],
    summary: '',
    root_causes: [],
    recommendations: [],
    news_context: [],
    anomalies: [],
    confidence: 0.82,
    clarification: null,
    sql_query: 'SELECT SUM(revenue) FROM user_data;',
    data_preview: [{ month: 'Jan', revenue: 1200 }],
    query_log_id: 'log-1',
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  useChatStore.getState().clearChat()
  vi.clearAllMocks()
})

describe('MessageStream — four message types (F3, specs/14 §4)', () => {
  it('renders a user message right-aligned with the file chip above the bubble', () => {
    useChatStore
      .getState()
      .addUserMessage('Why did revenue drop last week?', 'sales.csv')

    render(<MessageStream />)

    expect(screen.getByText('sales.csv')).toHaveClass('message__file-chip')
    expect(screen.getByText('Why did revenue drop last week?')).toHaveClass(
      'message__user-bubble',
    )
    expect(screen.getByText('Why did revenue drop last week?').closest('.message--user')).toHaveClass(
      'message--user',
    )
  })

  it('renders a normal answer: prose, visual grid, insights strip, trust footer, and news row', () => {
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        visuals: [
          { visual_type: 'metric', props: { label: 'Revenue', value: 4.2, change_pct: 12, direction: 'up' }, title: 'Revenue' },
          { visual_type: 'graph', props: { chart_type: 'line', labels: ['Jan'], datasets: [{ name: 'Revenue', values: [4.2] }] }, title: 'Revenue over time' },
        ],
        insights: ['Seasonality correlates with the drop.'],
        root_causes: ['A possible contributing factor is fewer new orders.'],
        recommendations: ['Consider expanding the discount window.'],
        news_context: ['Industry demand softened last quarter.'],
      }),
    )

    render(<MessageStream />)

    // 1. Answer prose.
    expect(screen.getByText('Total revenue was 4.2M this quarter.')).toHaveClass(
      'message__answer-prose',
    )

    // 2. Visual cards grid, min 240px; graph spans two columns. The metric
    //    label, grid title, and chart legend can all legitimately read
    //    "Revenue", so allow several matches.
    const grid = screen.getByLabelText('Visual results')
    expect(grid).toHaveClass('visual-cards-grid')
    expect(screen.getAllByText('Revenue').length).toBeGreaterThan(0)
    const graphCard = screen
      .getByText('Revenue over time')
      .closest('.visual-cards-grid__card')
    expect(graphCard).toHaveClass('visual-cards-grid__card--wide')

    // 3. Insights strip — collapsed by default, hedged "Possible factors".
    const stripToggle = screen.getByRole('button', { name: 'Possible factors' })
    expect(stripToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('A possible contributing factor is fewer new orders.')).not.toBeInTheDocument()

    // 4. Trust footer — always visible on a normal answer.
    expect(
      screen.getByRole('button', { name: 'Show the query' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Confidence' })).toHaveAttribute(
      'aria-valuenow',
      '0.82',
    )
    expect(
      screen.getByRole('button', { name: 'Flag this answer' }),
    ).toBeInTheDocument()

    // 5. News context row — only when non-empty, "from the web".
    expect(screen.getByText('From the web')).toBeInTheDocument()
    expect(screen.getByText('Industry demand softened last quarter.')).toBeInTheDocument()
  })

  it('expands the insights strip to show insights, root causes and recommendations', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        insights: ['A seasonal pattern stands out.'],
        root_causes: ['A possible contributing factor is fewer repeat orders.'],
        recommendations: ['Consider a retention campaign.'],
      }),
    )

    render(<MessageStream />)

    await user.click(screen.getByRole('button', { name: 'Possible factors' }))

    expect(
      screen.getByRole('button', { name: 'Possible factors' }),
    ).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('A possible contributing factor is fewer repeat orders.')).toBeInTheDocument()
    expect(screen.getByText('A seasonal pattern stands out.')).toBeInTheDocument()
    expect(screen.getByText('Consider a retention campaign.')).toBeInTheDocument()
  })

  it('"Show the query" reveals the SQL and raw data slice behind the answer', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(makeOutput())

    render(<MessageStream />)

    expect(screen.queryByText('SELECT SUM(revenue) FROM user_data;')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show the query' }))

    expect(screen.getByText('SELECT SUM(revenue) FROM user_data;')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Jan')).toBeInTheDocument()
  })

  it('"Flag this answer" calls the live /chat/flag write path', async () => {
    const user = userEvent.setup()
    vi.mocked(flagAnswer).mockResolvedValue({ query_log_id: 'log-1', flagged: true })
    useChatStore.getState().addAssistantMessage(makeOutput())

    render(<MessageStream />)

    await user.click(screen.getByRole('button', { name: 'Flag this answer' }))

    expect(flagAnswer).toHaveBeenCalledWith({ query_log_id: 'log-1' })
    expect(
      await screen.findByRole('button', { name: 'Flagged' }),
    ).toBeInTheDocument()
  })

  it('disables the flag with a tooltip when there is no query log to flag', () => {
    useChatStore
      .getState()
      .addAssistantMessage(makeOutput({ query_log_id: null, sql_query: null, data_preview: null }))

    render(<MessageStream />)

    const flag = screen.getByRole('button', { name: 'Flag this answer' })
    expect(flag).toBeDisabled()
    expect(flag).toHaveAttribute('title')
  })

  it('renders a clarification as a quick-pick prompt, not a chat answer', async () => {
    const user = userEvent.setup()
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: {
          question: 'Which time range should I compare?',
          options: ['This month vs last', 'This year vs last'],
        },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    expect(
      screen.getByText('Which time range should I compare?'),
    ).toHaveClass('message__clarification-question')

    // No answer block, no trust footer — nothing to verify yet.
    expect(
      screen.queryByRole('button', { name: 'Show the query' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Flag this answer' }),
    ).not.toBeInTheDocument()

    // Tapping an option sends it verbatim as the next user message.
    await user.click(
      screen.getByRole('button', { name: 'This month vs last' }),
    )

    const messages = useChatStore.getState().messages
    const last = messages[messages.length - 1]
    expect(last.role).toBe('user')
    if (last.role === 'user') {
      expect(last.content).toBe('This month vs last')
    }
  })

  it('renders the neutral fallback notice for a degraded response', () => {
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        confidence: 0,
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    expect(
      screen.getByText("Couldn't produce a reliable answer for that").closest(
        '.message--fallback',
      ),
    ).toBeInTheDocument()
    // No trust footer on a fallback.
    expect(
      screen.queryByRole('button', { name: 'Show the query' }),
    ).not.toBeInTheDocument()
  })

  it('renders the window-exhausted inline 429 notice with its reset countdown', async () => {
    useChatStore.getState().addSystemNotice('window-exhausted', Date.now() + 4 * 60 * 60 * 1000)

    render(<MessageStream />)

    expect(
      screen.getByText(/You've used your 4 questions for this 6-hour window/),
    ).toBeInTheDocument()
    // Live countdown to the window reset — the input stays enabled (§5.6).
    const remaining = await screen.findByText(
      (_text, element) => element?.tagName === 'STRONG' && /^in /.test(element.textContent ?? ''),
    )
    expect(remaining.textContent).toMatch(/^in \d/)
  })

  it('renders the permanent lifetime-cap card with the inline contact form, distinct from the window notice', () => {
    useChatStore.getState().addSystemNotice('lifetime-cap')

    render(<MessageStream />)

    expect(
      screen.getByText("You've reached the 100-question limit for now").closest(
        '.lifetime-cap-notice__heading',
      ),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Message')).toBeInTheDocument()
  })

  it('the lifetime-cap form POSTs /contact and shows the thanks message', async () => {
    vi.mocked(sendContact).mockResolvedValue({ message: "Thanks — we'll be in touch." })
    useChatStore.getState().addSystemNotice('lifetime-cap')
    const user = userEvent.setup()
    render(<MessageStream />)

    await user.type(screen.getByLabelText('Name'), 'Ada')
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Message'), 'I need more questions.')
    await user.click(screen.getByRole('button', { name: 'Tell us' }))

    expect(sendContact).toHaveBeenCalledWith({
      name: 'Ada',
      email: 'ada@example.com',
      message: 'I need more questions.',
    })
    expect(await screen.findByText("Thanks — we'll be in touch.")).toBeInTheDocument()
  })

  it('shows the named cold-start state on a session first request', () => {
    useChatStore.getState().setPending('cold-start')
    render(<MessageStream />)

    expect(
      screen.getByText('Waking up the server — first load can take up to a minute'),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Waking up the server' })).toBeInTheDocument()
  })
})