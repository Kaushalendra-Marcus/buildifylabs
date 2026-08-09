import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './App.css'

/**
 * App root (F0) — the router (react-router, F0 decision) is wired with a
 * single placeholder route that proves the design tokens + router work. The
 * auth screens (F1) and the Chat Workspace (F2) replace this placeholder; they
 * drop in as new routes without restructuring this file.
 */
function FoundationsPlaceholder() {
  return (
    <main className="app-shell">
      <p className="app-shell__brand">BuildifyLabs</p>
      <p className="app-shell__note">
        Foundations are in — auth screens land in F1.
      </p>
    </main>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<FoundationsPlaceholder />} />
        <Route path="*" element={<FoundationsPlaceholder />} />
      </Routes>
    </BrowserRouter>
  )
}
