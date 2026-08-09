/**
 * UploadPopover (F5, specs/14 §5.3 / specs/04) tests — drag-drop + browse
 * hint ("CSV, PDF, or XLSX"), the plan-accurate size hint (3MB free / 10MB
 * pro), the file list with status chips (processing/completed/failed), a
 * `failed` chip surfacing the stored reason, and an upload POSTing via the
 * live api/files seam and marking the active file for the next user bubble.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UploadPopover } from './UploadPopover'
import { useChatStore } from './chat-store'
import { useAuthStore } from '../auth/auth-store'
import { listFiles, uploadFile } from '../../api/files'
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
  useAuthStore.getState().signOut()
  vi.clearAllMocks()
})

describe('UploadPopover (F5 §5.3)', () => {
  it('lists the user files with status chips and surfaces a failed reason', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([
      fileResponse({ id: 'u2', file_name: 'customers.csv', status: 'completed' }),
      fileResponse({ id: 'u1', file_name: 'broken.pdf', status: 'failed', error: 'parsing not supported yet' }),
    ])

    render(<UploadPopover onClose={() => {}} />)

    expect(await screen.findByText('customers.csv')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toHaveAttribute('data-status', 'completed')
    expect(screen.getByText('broken.pdf')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toHaveAttribute('data-status', 'failed')
    expect(screen.getByText('parsing not supported yet')).toBeInTheDocument()
  })

  it('shows a 3 MB cap for the free plan and 10 MB for pro', () => {
    signedInAs('free')
    render(<UploadPopover onClose={() => {}} />)
    expect(screen.getByText('CSV, PDF, or XLSX · 3 MB max')).toBeInTheDocument()
  })

  it('uploads via the browse picker, marking the file as the active one', async () => {
    signedInAs('pro')
    vi.mocked(listFiles).mockResolvedValue([])
    const created = fileResponse({ id: 'up-new', file_name: 'revenue.csv', status: 'processing' })
    vi.mocked(uploadFile).mockResolvedValue(created)
    vi.mocked(listFiles).mockResolvedValue([created])
    const user = userEvent.setup()
    render(<UploadPopover onClose={() => {}} />)

    expect(screen.getByText('CSV, PDF, or XLSX · 10 MB max')).toBeInTheDocument()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['a,b'], 'revenue.csv', { type: 'text/csv' })
    await user.upload(input, file)

    expect(uploadFile).toHaveBeenCalledWith(file)
    // The completed/current upload becomes the file chip above the next
    // user message (specs/14 §4.1).
    expect(useChatStore.getState().activeFileName).toBe('revenue.csv')
  })
})