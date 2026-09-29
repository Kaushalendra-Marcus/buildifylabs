/**
 * DocumentPicker tests — PDF multi-select chips for the Your data / Both
 * scopes. Only completed PDFs are pickable; toggling narrows the store
 * selection; guests, Live web scope, and empty libraries render nothing.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DocumentPicker } from './DocumentPicker'
import { useDocFilterStore } from './doc-filter-store'
import { useScopeStore } from './scope-store'
import { useAuthStore } from '../auth/auth-store'
import { listFiles } from '../../api/files'
import type { FileResponse } from '../../types/upload'
import type { Plan } from '../../types'

vi.mock('../../api/files', () => ({
  listFiles: vi.fn(),
}))

function fileResponse(overrides: Partial<FileResponse> = {}): FileResponse {
  return {
    id: 'pdf-1',
    file_name: 'report.pdf',
    file_type: 'application/pdf',
    file_size: 1024,
    status: 'completed',
    pinecone_namespace: 'vector:pdf-1',
    error: null,
    created_at: '2026-09-29T00:00:00Z',
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
  useScopeStore.setState({ scope: 'own_data' })
  useDocFilterStore.setState({ selectedFileIds: [] })
  useAuthStore.getState().signOut()
  vi.clearAllMocks()
})

describe('DocumentPicker', () => {
  it('lists completed PDFs and toggles selection', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([
      fileResponse({ id: 'pdf-1', file_name: 'a.pdf' }),
      fileResponse({ id: 'pdf-2', file_name: 'b.pdf' }),
      fileResponse({ id: 'bad', file_name: 'broken.pdf', status: 'failed' }),
      fileResponse({ id: 'csv-1', file_name: 'data.csv', file_type: 'text/csv' }),
    ])
    render(<DocumentPicker />)

    expect(await screen.findByRole('button', { name: 'a.pdf' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'b.pdf' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'broken.pdf' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'data.csv' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'a.pdf' }))
    expect(useDocFilterStore.getState().selectedFileIds).toEqual(['pdf-1'])
    expect(screen.getByRole('button', { name: 'a.pdf' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByText('Documents (1/2)')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(useDocFilterStore.getState().selectedFileIds).toEqual([])
  })

  it('renders nothing when there are no completed PDFs', async () => {
    signedInAs('free')
    vi.mocked(listFiles).mockResolvedValue([])
    const { container } = render(<DocumentPicker />)
    await waitFor(() => expect(listFiles).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for the Live web scope', async () => {
    signedInAs('free')
    useScopeStore.setState({ scope: 'live_web' })
    vi.mocked(listFiles).mockResolvedValue([fileResponse()])
    const { container } = render(<DocumentPicker />)
    await waitFor(() => expect(listFiles).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for guests', async () => {
    signedInAs('guest')
    vi.mocked(listFiles).mockResolvedValue([fileResponse()])
    const { container } = render(<DocumentPicker />)
    await waitFor(() => expect(listFiles).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
