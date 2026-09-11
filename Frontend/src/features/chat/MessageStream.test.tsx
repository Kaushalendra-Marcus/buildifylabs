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
import { stripPriorOptionAnswer } from './messages/clarification-thread'
import type { PipelineOutput } from '../../types/chat'
import { flagAnswer, sendQuery } from '../../api/chat'
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
  useChatStore.getState().setHasData(null)
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
    const stripToggle = screen.getByRole('button', { name: /Insights · 3 items/ })
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

    await user.click(screen.getByRole('button', { name: /Insights · 3 items/ }))

    expect(
      screen.getByRole('button', { name: /Insights · 3 items/ }),
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
    vi.mocked(sendQuery).mockResolvedValue(
      makeOutput({ answer: 'Revenue comparison data: This month vs last month' })
    )
    
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

    // Selecting an option arms it; Send submits and gets a response.
    // The Send button stays disabled until something is selected.
    expect(
      screen.getByRole('button', { name: 'Send selected' }),
    ).toBeDisabled()
    await user.click(
      screen.getByRole('button', { name: 'This month vs last' }),
    )
    expect(
      screen.getByRole('button', { name: 'This month vs last' }),
    ).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Send selected' }))

    // Wait for async query completion
    await new Promise(resolve => setTimeout(resolve, 100))

    const messages = useChatStore.getState().messages
    // After clarification, we should have user message followed by assistant response
    expect(messages.length).toBeGreaterThanOrEqual(3)
    const userMsg = messages[messages.length - 2]
    expect(userMsg.role).toBe('user')
    if (userMsg.role === 'user') {
      expect(userMsg.content).toContain('This month vs last')
    }
  })

  it('sends several selected options joined in one follow-up', async () => {
    const user = userEvent.setup()
    vi.mocked(sendQuery).mockResolvedValue(
      makeOutput({ answer: 'Multi response' })
    )

    useChatStore.getState().addUserMessage('compare agents?')
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: {
          question: 'Which metrics?',
          options: ['Total users', 'Active users', 'Revenue'],
        },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    await user.click(screen.getByRole('button', { name: 'Total users' }))
    await user.click(screen.getByRole('button', { name: 'Active users' }))
    // Toggling twice deselects.
    await user.click(screen.getByRole('button', { name: 'Revenue' }))
    await user.click(screen.getByRole('button', { name: 'Revenue' }))
    expect(
      screen.getByRole('button', { name: 'Revenue' }),
    ).toHaveAttribute('aria-pressed', 'false')

    await user.click(screen.getByRole('button', { name: 'Send 2 selected' }))

    await new Promise((resolve) => setTimeout(resolve, 100))

    const messages = useChatStore.getState().messages
    const userMsg = messages[messages.length - 2]
    expect(userMsg.role).toBe('user')
    if (userMsg.role === 'user') {
      expect(userMsg.content).toBe('compare agents? - Total users, Active users')
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
      screen.getByText(/You've used your 100 questions for this 6-hour window/),
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
    // Real flow: the composer appends the user message, THEN marks pending —
    // so the cold-start card renders inside the (non-empty) list.
    useChatStore.getState().addUserMessage('Why did revenue drop last week?')
    useChatStore.getState().setPending('cold-start')
    render(<MessageStream />)

    expect(
      screen.getByText('Waking up the server — first load can take up to a minute'),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Waking up the server' })).toBeInTheDocument()
  })

  it('shows the small inline thinking indicator under the user message, distinct from cold start', () => {
    useChatStore.getState().addUserMessage('How did Q2 go?')
    useChatStore.getState().setPending('thinking')
    render(<MessageStream />)

    expect(
      screen.getByRole('status', { name: 'Assistant is thinking' }),
    ).toBeInTheDocument()
    // Distinct from §5.7 cold start: no named wake-up card.
    expect(
      screen.queryByText('Waking up the server — first load can take up to a minute'),
    ).not.toBeInTheDocument()
  })

  it('routes a no-data fallback through the no-data messaging, not a generic empty', () => {
    // User provably has no data (F6, 07 edge case 2) — a degraded response is
    // the no-data messaging, not "Couldn't produce a reliable answer".
    useChatStore.getState().setHasData(false)
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
      screen.getByText(
        /You haven't uploaded any data yet.*get started/,
      ).closest('.message--no-data'),
    ).toBeInTheDocument()
  })

  it('renders live streaming prose while the answer generates', () => {
    useChatStore.getState().addUserMessage('How did Q2 go?')
    useChatStore.getState().setPending('thinking')
    useChatStore.getState().appendStreamingText('Revenue grew ')
    useChatStore.getState().appendStreamingText('5% this quarter.')

    render(<MessageStream />)

    const block = screen.getByRole('status', { name: 'Answer streaming' })
    expect(block).toHaveClass('message--assistant-streaming')
    expect(block).toHaveTextContent('Revenue grew 5% this quarter.')
  })

  it('renders no streaming block once the text is cleared', () => {
    useChatStore.getState().addUserMessage('How did Q2 go?')
    useChatStore.getState().setPending('thinking')
    useChatStore.getState().appendStreamingText('partial')
    useChatStore.getState().clearStreamingText()

    render(<MessageStream />)

    expect(
      screen.queryByRole('status', { name: 'Answer streaming' }),
    ).not.toBeInTheDocument()
  })
})

describe('MessageStream — intelligence styling', () => {
  it('stamps a timestamp under the user bubble', () => {
    useChatStore.getState().addUserMessage('Why did revenue drop last week?')

    render(<MessageStream />)

    const time = screen.getByText(/AM|PM/, { selector: 'time' })
    expect(time).toHaveClass('message__time')
    expect(time).toHaveAttribute('dateTime')
  })

  it('labels answers with the Buildify Intelligence identity and a trust time', () => {
    useChatStore.getState().addAssistantMessage(makeOutput())

    render(<MessageStream />)

    expect(screen.getByText('Buildify Intelligence')).toBeInTheDocument()
    expect(
      document.querySelector('.trust-footer__time'),
    ).toBeInTheDocument()
  })

  it('labels clarifications with the identity row and an eyebrow', () => {
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: {
          question: 'Which time range should I compare?',
          options: ['This month vs last'],
        },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    expect(screen.getByText('Buildify Intelligence')).toBeInTheDocument()
    expect(screen.getByText('Clarification needed')).toBeInTheDocument()
  })

  it('renders a clarification with no preset options as question only, no pills', () => {
    // The backend coerces a model-emitted options:null to [] — the question
    // must still render, with no option buttons and no trust footer.
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: {
          question: 'Which AI business should I compare?',
          options: [],
        },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    expect(
      screen.getByText('Which AI business should I compare?'),
    ).toHaveClass('message__clarification-question')
    expect(
      screen.queryByRole('button', { name: 'Show the query' }),
    ).not.toBeInTheDocument()
  })

  it('sends a typed custom reply as a follow-up like a pill tap', async () => {
    const user = userEvent.setup()
    vi.mocked(sendQuery).mockResolvedValue(
      makeOutput({ answer: 'Custom answer response' })
    )

    useChatStore.getState().addUserMessage('trending startups?')
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: {
          question: 'Which sector?',
          options: ['Fintech'],
        },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    // Both affordances render together: the preset pill and the free-text box.
    expect(
      screen.getByRole('button', { name: 'Fintech' }),
    ).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('Type your answer…'),
    ).toBeInTheDocument()

    await user.type(
      screen.getByPlaceholderText('Type your answer…'),
      'My own sector pick',
    )
    await user.click(screen.getByRole('button', { name: 'Send custom answer' }))

    await new Promise((resolve) => setTimeout(resolve, 100))

    const messages = useChatStore.getState().messages
    const userMsg = messages[messages.length - 2]
    expect(userMsg.role).toBe('user')
    if (userMsg.role === 'user') {
      expect(userMsg.content).toContain('My own sector pick')
    }
  })

  it('renders **bold** model markers as strong, not literally', () => {
    useChatStore.getState().addAssistantMessage(
      makeOutput({ answer: 'top harness is **Claude Code** today' }),
    )

    render(<MessageStream />)

    const bold = screen.getByText('Claude Code')
    expect(bold.tagName).toBe('STRONG')
    expect(screen.queryByText('**Claude Code**')).not.toBeInTheDocument()
  })

  it('renders follow-up chips that send as new questions', async () => {
    const user = userEvent.setup()
    vi.mocked(sendQuery).mockResolvedValue(
      makeOutput({ answer: 'Follow-up response' })
    )

    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: 'Revenue held steady.',
        followups: ['Break it down by region?'],
      }),
    )

    render(<MessageStream />)

    await user.click(
      screen.getByRole('button', { name: 'Break it down by region?' }),
    )

    await new Promise((resolve) => setTimeout(resolve, 100))

    expect(vi.mocked(sendQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'Break it down by region?' }),
    )
    const messages = useChatStore.getState().messages
    const userMsg = messages[messages.length - 2]
    expect(userMsg.role).toBe('user')
    if (userMsg.role === 'user') {
      expect(userMsg.content).toBe('Break it down by region?')
    }
  })

  it('renders nothing extra when an answer has no follow-ups', () => {
    useChatStore.getState().addAssistantMessage(makeOutput())

    render(<MessageStream />)

    expect(
      screen.queryByLabelText('Suggested follow-up questions'),
    ).not.toBeInTheDocument()
  })
})

describe('stripPriorOptionAnswer', () => {
  it('strips a trailing previously-picked option', () => {
    expect(stripPriorOptionAnswer('base query - Old pick', ['Old pick'])).toBe(
      'base query',
    )
  })

  it('keeps free-typed answers that match no prior option', () => {
    expect(stripPriorOptionAnswer('base query - my own words', ['Old pick'])).toBe(
      'base query - my own words',
    )
  })

  it('leaves content without any prior suffix untouched', () => {
    expect(stripPriorOptionAnswer('fresh question?', ['Old pick'])).toBe(
      'fresh question?',
    )
  })

  it('chained rounds send base-plus-new-pick, never double-appended', async () => {
    const user = userEvent.setup()
    vi.mocked(sendQuery).mockResolvedValue(
      makeOutput({ answer: 'Chained response' })
    )

    useChatStore.getState().addUserMessage('startup ideas - AI sector')
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: { question: 'Which sector?', options: ['AI sector'] },
        sql_query: null,
        data_preview: null,
      }),
    )
    useChatStore.getState().addAssistantMessage(
      makeOutput({
        answer: '',
        visuals: [],
        clarification: { question: 'Which metric?', options: ['Revenue ways'] },
        sql_query: null,
        data_preview: null,
      }),
    )

    render(<MessageStream />)

    await user.click(screen.getByRole('button', { name: 'Revenue ways' }))
    // Two clarification blocks render; Send on the current (last) one.
    const sendButtons = screen.getAllByRole('button', { name: 'Send selected' })
    await user.click(sendButtons[sendButtons.length - 1])

    await new Promise((resolve) => setTimeout(resolve, 100))

    const messages = useChatStore.getState().messages
    const userMsg = messages[messages.length - 2]
    expect(userMsg.role).toBe('user')
    if (userMsg.role === 'user') {
      expect(userMsg.content).toBe('startup ideas - Revenue ways')
    }
  })
})
