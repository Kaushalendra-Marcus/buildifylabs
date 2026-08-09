/**
 * EmptyThread (F6, specs/14 §6) tests — the two empty-thread states:
 *   - guest: invite a question, NO upload affordance at all
 *   - registered + no files: invite an upload ("Add a CSV, PDF, or
 *     spreadsheet to get started"), popover one tap away
 *   - registered + files: invite a question
 * Plus the MessageStream wiring: a zero-message thread shows the EmptyThread.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EmptyThread } from './messages/EmptyThread'
import { MessageStream } from './MessageStream'
import { useChatStore } from './chat-store'
import { useAuthStore } from '../auth/auth-store'
import { listFiles } from '../../api/files'
import type { FileResponse, Plan } from '../../types'

vi.mock('../../api/files', () => ({
  uploadFile: vi.fn(),
  listFiles: vi.fn(),
}))

function fileResponse(overrides: Partial<FileResponse> = {}): FileResponse {
  return {
    id: 'upload-1',
    file_name: 'sales.csv',
    file_type: 'text/csv',
    file_size: 1200,
    status: 'completed',
    pinecone_namespace: null,
    error: null,
    created_at: '2026-08-09T10:00:00Z',
    ...overrides,
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
  useChatStore.getState().setHasData(null)
  useAuthStore.getState().signOut()
  vi.clearAllMocks()
})

describe('EmptyThread — guest (F6, specs/14 §6)', () => {
  it('invites a question with NO upload affordance at all', async () => {
    signedInAs('guest')
    render(<EmptyThread />)

    expect(
      await screen.findByText('Ask anything about your business data'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('Add a CSV, PDF, or spreadsheet to get started'),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a file' })).not.toBeInTheDocument()
    // Guest has no data by construction.
    expect(useChatStore.getState().hasData).toBe(false)
  })
})

describe('EmptyThread — registered, no files (F6, specs/14 §6)', () => {
  it('invites an upload first', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    render(<EmptyThread />)

    expect(
      await screen.findByText('Add a CSV, PDF, or spreadsheet to get started'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add a file' })).toBeInTheDocument()
  })

  it('opens the upload popover from the invite', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    const user = userEvent.setup()
    render(<EmptyThread />)

    await user.click(
      await screen.findByRole('button', { name: 'Add a file' }),
    )
    expect(
      screen.getByRole('dialog', { name: 'Upload files' }),
    ).toBeInTheDocument()
  })

  it('a completed upload flips the thread to the question invite (hasData=true)', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    render(<EmptyThread />)

    // Wait for the initial no-files check to settle, then flip the store.
    await screen.findByText('Add a CSV, PDF, or spreadsheet to get started')
    useChatStore.getState().setHasData(true)

    expect(
      await screen.findByText('Ask anything about your business data'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('Add a CSV, PDF, or spreadsheet to get started'),
    ).not.toBeInTheDocument()
  })
})

describe('EmptyThread — registered, has files (F6, specs/14 §6)', () => {
  it('invites a question directly', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([fileResponse()])
    render(<EmptyThread />)

    expect(
      await screen.findByText('Ask anything about your business data'),
    ).toBeInTheDocument()
    expect(useChatStore.getState().hasData).toBe(true)
  })
})

describe('MessageStream wiring (F6 §6)', () => {
  it('renders the EmptyThread when the thread has no messages', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    render(<MessageStream />)

    expect(
      await screen.findByText('Add a CSV, PDF, or spreadsheet to get started'),
    ).toBeInTheDocument()
  })
})
