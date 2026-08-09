import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// vitest config has no `globals: true`, so RTL's auto-cleanup never registers
// itself — without this, DOM from one test leaks into the next within a file.
afterEach(() => {
  cleanup()
})
