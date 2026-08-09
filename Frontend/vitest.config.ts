import { defineConfig } from 'vitest/config'

// Vitest config (F0 decision: Vitest + React Testing Library). The `test`
// block is consumed by `npm test` / `vitest`; `vite build` ignores it.
export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
