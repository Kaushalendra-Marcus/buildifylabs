/**
 * AuthLayout tests — the shared shell: brand + form outlet floating over the
 * `public/login.png` backdrop (shared by signin/signup).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthLayout } from './AuthLayout'

function renderLayout() {
  const { container } = render(
    <MemoryRouter initialEntries={['/signin']}>
      <Routes>
        <Route element={<AuthLayout />}>
          <Route path="/signin" element={<p>Sign-in form</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
  return container
}

describe('AuthLayout', () => {
  it('renders the brand, the form outlet, and the login illustration', () => {
    const container = renderLayout()

    expect(screen.getByText('BuildifyLabs')).toBeInTheDocument()
    expect(screen.getByText('Sign-in form')).toBeInTheDocument()
    const art = container.querySelector<HTMLImageElement>('.auth-art__image')
    expect(art?.getAttribute('src')).toBe('/login.png')
    expect(screen.getByText('Your business, explained')).toBeInTheDocument()
  })
})
