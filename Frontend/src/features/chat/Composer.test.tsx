/**
 * Composer (F5, specs/14 §5) component tests — acceptance: multiline input
 * with the real-example placeholder (5.1), 3-way source-scope segmented
 * control defaulting to/persisting "Your data" with a gated hint for
 * Live web/Both (5.2), upload button ABSENT — not disabled — for guests (5.3),
 * Send disabled ONLY when empty — never by quota (5.4), and both 429 states
 * flowing from a rejected `/chat` into stream notices (5.6).
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Composer } from './Composer'
import { useChatStore } from './chat-store'
import { useQuotaStore } from './quota-store'
import { useScopeStore } from './scope-store'
import { useAuthStore } from '../auth/auth-store'
import { sendQuery } from '../../api/chat'
import { listFiles } from '../../api/files'
import { ApiError } from '../../lib/http'
import type { PipelineOutput } from '../../types/chat'
import type { Plan } from '../../types'

vi.mock('../../api/chat', () => ({
  sendQuery: vi.fn(),
  flagAnswer: vi.fn(),
}))

vi.mock('../../api/files', () => ({
  uploadFile: vi.fn(),
  listFiles: vi.fn(),
}))

function makeOutput(): PipelineOutput {
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
    data_preview: [],
    query_log_id: 'log-1',
  }
}

function signedInAs(plan: Plan) {
  useAuthStore.getState().setSession({
    user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan },
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
  })
}

beforeEach(() => {
  localStorage.clear()
  useChatStore.getState().clearChat()
  useQuotaStore.setState({
    questionsInWindow: 0,
    windowStartedAt: null,
    questionsLifetime: 0,
  })
  useScopeStore.setState({ scope: 'own_data' })
  useAuthStore.getState().signOut()
  vi.clearAllMocks()
})

describe('Composer (F5, specs/14 §5)', () => {
  it('renders the auto-grow input, placeholder, scope selector and a send button disabled when empty', () => {
    signedInAs('free')
    render(<Composer />)

    expect(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
    ).toBeInTheDocument()
    const group = screen.getByRole('group', { name: 'Source scope' })
    expect(group).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Your data' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Live web' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Both' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  })

  it('sends the text + chosen scope, records quota, and appends user + assistant messages', async () => {
    signedInAs('free')
    vi.mocked(sendQuery).mockResolvedValue(makeOutput())
    const user = userEvent.setup()
    render(<Composer />)

    await user.type(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
      'Why did revenue drop last week?',
    )
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(sendQuery).toHaveBeenCalledWith({
      query: 'Why did revenue drop last week?',
      source_scope: 'own_data',
    })

    const quota = useQuotaStore.getState()
    expect(quota.questionsInWindow).toBe(1)
    expect(quota.questionsLifetime).toBe(1)

    const messages = useChatStore.getState().messages
    expect(messages[0].role).toBe('user')
    expect(messages[1].role).toBe('assistant')
    expect(messages[1].role === 'assistant' && messages[1].output.answer).toBe(
      'Total revenue was 4.2M this quarter.',
    )
  })

  it('Enter (without Shift) also sends the draft', async () => {
    signedInAs('free')
    vi.mocked(sendQuery).mockResolvedValue(makeOutput())
    const user = userEvent.setup()
    render(<Composer />)

    await user.type(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
      'Hello{Enter}',
    )
    expect(sendQuery).toHaveBeenCalledWith({ query: 'Hello', source_scope: 'own_data' })
  })

  it('the upload button is ABSENT for guest plans (nothing shown, not disabled)', () => {
    signedInAs('guest')
    render(<Composer />)
    expect(screen.queryByRole('button', { name: 'Upload files' })).not.toBeInTheDocument()
  })

  it('shows the upload button for registered plans and opens the popover hints', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    const user = userEvent.setup()
    render(<Composer />)

    const upload = screen.getByRole('button', { name: 'Upload files' })
    expect(upload).toBeInTheDocument()

    await user.click(upload)
    expect(screen.getByRole('dialog', { name: 'Upload files' })).toBeInTheDocument()
    expect(screen.getByText('CSV, PDF, or XLSX · 3 MB max')).toBeInTheDocument()
  })

  it('persists the source-scope selection across queries (and reloads)', async () => {
    signedInAs('pro')
    const user = userEvent.setup()
    render(<Composer />)

    await user.click(screen.getByRole('button', { name: 'Live web' }))

    expect(screen.getByRole('button', { name: 'Live web' })).toHaveAttribute('aria-pressed', 'true')
    // Gated hint, not a silent switch (B7): answers fall back to own data.
    expect(
      screen.getByText(/Live web and Both aren't available yet/),
    ).toBeInTheDocument()
    // Persisted to localStorage (zustand persist — reloads keep the choice).
    expect(localStorage.getItem('buildifylabs.source-scope')).toContain('"live_web"')

    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  })

  it('a rejected send with the window 429 shows the inline window-exhausted notice and keeps the input enabled', async () => {
    signedInAs('free')
    // Window started 2h ago → reset in ~4h.
    useQuotaStore.setState({
      windowStartedAt: Date.now() - 2 * 60 * 60 * 1000,
      questionsLifetime: 0,
    })
    vi.mocked(sendQuery).mockRejectedValue(
      new ApiError(429, {
        detail:
          "You've used your 4 questions for this 6-hour window. More unlock at 17:00.",
      }),
    )
    const user = userEvent.setup()
    render(<Composer />)

    await user.type(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
      'one more',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    // Quota mirror reflects the exhausted window.
    expect(useQuotaStore.getState().questionsInWindow).toBe(4)
    const last = useChatStore.getState().messages.at(-1)
    expect(last).toMatchObject({ role: 'system', kind: 'window-exhausted' })

    // The notice carries the reset-time countdown timestamp.
    if (last?.role === 'system' && last.kind === 'window-exhausted') {
      expect(last.resetAt).toEqual(expect.any(Number))
    }
    // The send button is NEVER disabled by quota — the input stays enabled
    // for the next window (§5.4/§5.6). (The button itself is disabled again
    // only because the submitted draft was cleared.)
    expect(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
    ).toBeEnabled()
  })

  it('a lifetime-cap 429 drives the permanent lifetime card with the contact form', async () => {
    signedInAs('free')
    vi.mocked(sendQuery).mockRejectedValue(
      new ApiError(429, {
        detail: "You've reached the 100-question limit for now.",
        contact_form: true,
      }),
    )
    const user = userEvent.setup()
    render(<Composer />)

    await user.type(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
      'still want to ask',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(useQuotaStore.getState().questionsLifetime).toBe(100)
    expect(useChatStore.getState().messages.at(-1)).toMatchObject({
      role: 'system',
      kind: 'lifetime-cap',
    })
    // The notice is a store notice the stream renders — nothing in the
    // composer itself blocks the input (it stays enabled).
    expect(
      screen.getByPlaceholderText('Why did revenue drop last week?'),
    ).toBeEnabled()
  })
})