import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App shell (F0 foundations)', () => {
  it('renders the placeholder without crashing', () => {
    render(<App />)
    expect(screen.getByText('BuildifyLabs')).toBeTruthy()
  })
})
