import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// vitest config has no `globals: true`, so RTL's auto-cleanup never registers
// itself — without this, DOM from one test leaks into the next within a file.
afterEach(() => {
  cleanup()
})

// jsdom doesn't implement ResizeObserver; Recharts' ResponsiveContainer needs
// it to measure the chart area, so stub it for the F4 graph tests.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
