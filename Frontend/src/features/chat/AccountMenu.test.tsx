/**
 * AccountMenu tests — the header profile section: identity block (name +
 * email, or Guest + plan), the four items (Plan & billing, Data sources,
 * Contact us, Sign out), and the three dialogs on live seams.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'
import { useAuthStore } from '../auth/auth-store'
import { useQuotaStore } from './quota-store'
import { listFiles } from '../../api/files'
import { sendContact } from '../../api/contact'

vi.mock('../../api/files', () => ({
  listFiles: vi.fn(),
  uploadFile: vi.fn(),
  getFile: vi.fn(),
}))

vi.mock('../../api/contact', () => ({
  sendContact: vi.fn(),
}))

function signedInAsRegistered() {
  useAuthStore.getState().setSession({
    user: { id: 'user-1', email: 'jane@acme.co', name: 'Jane Doe', plan: 'free' },
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
  })
}

function signedInAsGuest() {
  useAuthStore.getState().setSession({
    user: { id: 'guest-1', email: null, name: null, plan: 'guest' },
    access_token: 'access-guest',
    refresh_token: null,
    token_type: 'bearer',
  })
}

beforeEach(() => {
  localStorage.clear()
  useAuthStore.getState().signOut()
  useQuotaStore.getState().reset()
  vi.clearAllMocks()
})

describe('AccountMenu — profile section', () => {
  it('shows the identity block and all four items for a registered user', async () => {
    const user = userEvent.setup()
    signedInAsRegistered()
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))

    expect(document.querySelector('.account-menu__identity-name')).toHaveTextContent('Jane Doe')
    expect(document.querySelector('.account-menu__identity-email')).toHaveTextContent('jane@acme.co')
    expect(screen.getByRole('menuitem', { name: 'Plan & billing' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Data sources' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Contact us' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('signs out from the menu', async () => {
    const user = userEvent.setup()
    signedInAsRegistered()
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))
    await user.click(screen.getByRole('menuitem', { name: 'Sign out' }))

    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().status).toBe('unauthenticated')
  })

  it('shows Guest + plan for a guest session', async () => {
    const user = userEvent.setup()
    signedInAsGuest()
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))

    expect(document.querySelector('.account-menu__identity-name')).toHaveTextContent('Guest')
    expect(document.querySelector('.account-menu__identity-email')).toHaveTextContent('guest plan')
  })

  it('opens the plan dialog with the real plan and quota usage', async () => {
    const user = userEvent.setup()
    signedInAsRegistered()
    useQuotaStore.getState().recordQuestion()
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))
    await user.click(screen.getByRole('menuitem', { name: 'Plan & billing' }))

    expect(screen.getByRole('dialog', { name: 'Plan & billing' })).toBeInTheDocument()
    expect(screen.getByText('Current plan')).toBeInTheDocument()
    expect(screen.getByText(/99 of 100 this window/)).toBeInTheDocument()
  })

  it('opens the data-sources dialog with the live file list', async () => {
    const user = userEvent.setup()
    signedInAsRegistered()
    vi.mocked(listFiles).mockResolvedValue([
      {
        id: 'f-1',
        file_name: 'sales.csv',
        file_type: 'csv',
        file_size: 1234,
        status: 'completed',
        pinecone_namespace: null,
        error: null,
        created_at: new Date().toISOString(),
      },
    ])
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))
    await user.click(screen.getByRole('menuitem', { name: 'Data sources' }))

    expect(await screen.findByText('sales.csv')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
  })

  it('sends the contact form and shows the thanks message', async () => {
    const user = userEvent.setup()
    signedInAsRegistered()
    vi.mocked(sendContact).mockResolvedValue({ message: "Thanks — we'll be in touch." })
    render(<AccountMenu />)

    await user.click(screen.getByRole('button', { name: 'Account' }))
    await user.click(screen.getByRole('menuitem', { name: 'Contact us' }))

    // Name/email prefill from the signed-in identity — clear before typing.
    await user.clear(screen.getByLabelText('Name'))
    await user.clear(screen.getByLabelText('Email'))
    await user.type(screen.getByLabelText('Name'), 'Jane')
    await user.type(screen.getByLabelText('Email'), 'jane@acme.co')
    await user.type(screen.getByLabelText('Message'), 'Need a higher limit.')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(sendContact).toHaveBeenCalledWith({
      name: 'Jane',
      email: 'jane@acme.co',
      message: 'Need a higher limit.',
    })
    expect(await screen.findByText("Thanks — we’ll be in touch.")).toBeInTheDocument()
  })
})
