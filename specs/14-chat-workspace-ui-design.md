# 14 — Frontend Design: Chat Workspace (Main Page)

**Status:** 📋 Proposed — first design pass for `buildifylabs/Frontend`, which has no UI yet
(`Frontend/CLAUDE.md` §1: still the unmodified Vite/React/TS scaffold). This is **independent of**
the separate `Buldify-Labs-AI-Business-Intelligence-Workspace` Next.js project — that codebase is
not being touched, moved, or reused here. It's referenced below only where it surfaces a real
lesson, as prior art, not as code or layout to carry over.
**Input:** the Figma file "Buildify Labs — Chat Workspace" was not reachable via Dev Mode MCP as of
this draft — see the note at the end of this file. This spec is grounded instead in specs
`01`/`02`/`04`/`06`/`07`/`10` and should be reconciled against the actual Figma frames once
accessible.
**Source files:** none yet — `Frontend/src/App.tsx` is still template boilerplate.

---

## 1. Problem statement

The Chat Workspace is the one primary authenticated screen of the product. A user asks a
plain-language question about their business data and gets back an answer, one or more visual
cards (`06`), and — when the pipeline can't answer confidently — a clarifying question instead of a
guess. Every response must let the user verify it, not just trust it (`10` §2). The screen also has
to carry the product's non-chat mechanics inline: usage-quota visibility, upload access (registered
users only), source-scope selection (`07`), and the two distinct near-limit UX cases (`02`).

Prior art exists in the separate, unrelated Next.js codebase: a bottom resizable drawer that
displays generated cards grouped by query or by company, with view-mode toggles and hover tooltips
so a user can recover which query produced which card. That's a real, working solution — but it's
also a symptom worth noting (see design principle 1) rather than a pattern to repeat.

## 2. Design principles

1. **Visuals live next to the question that produced them, not in a separate panel.** The prior-art
   project needed query-grouping, a "by query / by company" toggle, and hover tooltips specifically
   to recover which card came from which question. Rendering each response's cards inline, directly
   under that turn, makes traceability — `10` §2's core trust requirement — free instead of bolted
   on afterward.
2. **Every AI claim shows its confidence and its receipt.** `10` §2 calls "here's the query that
   produced this" the single most effective trust-building feature available. That affordance and a
   confidence indicator are one tap from every answer, never behind a settings toggle.
3. **The UI never lets the model guess quietly.** A `clarification` response (`06` FR7, `07` FR4)
   gets a visually distinct message type — quick-pick chips, not a plain text bubble — so it can't
   be mistaken for an answer the user should just accept.
4. **Quota and scope are ambient, not modal.** Rolling-window quota (`02`) and source-scope (`07`)
   are small, persistent controls near the composer — never a popup the user has to dismiss before
   asking a question.
5. **Design for the free-tier cold start.** `Frontend/docs/structure.md` already flags this: a bare
   spinner on a 30–40s Render cold start reads as broken. First load gets its own named state (§5.7).

## 3. Page structure

Three regions in one layout — no separate drawer or workspace panel:

```
┌───────────────────────────────────────────────────────────────┐
│ Header (56px) — logo, quota chip, plan badge, account, new chat│
├────────────┬──────────────────────────────────────────────────┤
│ Chat       │                                                  │
│ history    │           Message stream (scrolls)               │
│ rail       │                                                  │
│ (0 or      │                                                  │
│ 280px,     ├──────────────────────────────────────────────────┤
│ overlay on │  Composer — input, source-scope selector, upload, │
│ narrow     │  send (fixed to bottom of this column)            │
│ viewports) │                                                  │
└────────────┴──────────────────────────────────────────────────┘
```

- **Header** — logo/wordmark, quota chip (§5.5), plan badge (`AuthUserResponse.plan`:
  `guest`/`free`/`pro`), account menu, "new chat".
- **Chat history rail** — collapsible list of past threads; collapsed by default under 768px,
  rendered as an overlay rather than pushing content.
- **Message stream** — the only place visuals render. See §4.
- **Composer** — text input, source-scope selector (`07` FR3), upload button (registered users
  only, `04` FR1), send.

No resizable bottom panel, no separate "workspace" surface — a deliberate departure from the
prior-art project, per design principle 1.

## 4. Message stream — component spec

### 4.1 User message
Right-aligned, single line-height bubble, no card chrome. An uploaded file (if any) shows as a
small chip above the bubble, not inside it.

### 4.2 Assistant message — normal answer
A single left-aligned block, no bubble background — this is a structured response, not a short chat
turn:

1. **Answer text** (`PipelineOutput.answer`) — prose, full width.
2. **Visual cards** — one per `VisualOutput`, `visual_type ∈ {metric, graph, table, comparison,
   insight, alert, status}` (`06` FR3). Grid: `repeat(auto-fit, minmax(240px, 1fr))`; `graph` and
   `table` span two columns. (This sizing rule is the one thing worth keeping from the prior-art
   project — it's a real, already-solved layout problem, independent of the panel it used to live
   in.)
3. **Insights strip** (collapsed by default, expandable) — `insights[]`, `root_causes[]`,
   `recommendations[]`. Root causes and recommendations use hedged copy by construction (a "Possible
   factors" label, not "Why this happened") per `10` §2's causal-language requirement — a content
   rule as much as a layout one.
4. **Trust footer** (always visible, never collapsed) — three affordances in one row:
   - `Show the query` → expands the SQL and raw data slice behind the answer (`10` §2).
   - Confidence indicator — a small labeled meter. Render it only once `confidence` is
     schema-bounded to 0–1 (`06` §3 / edge case 4); don't display a raw unbounded value in the
     meantime.
   - `Flag this answer` → a one-line reason input, feeding `QueryLogs` (`10` §2). Disable with a
     tooltip rather than hiding it if that write path isn't live yet.
5. **News context** (`news_context[]`) — rendered only when non-empty, as a distinct "from the web"
   row, so it's visually clear what came from `live_web` scope (`07`) versus the user's own data.

### 4.3 Assistant message — clarification
Visually distinct from 4.2: an accent-colored left edge, the `question` text, and `options[]` as
tappable pill buttons — tapping one sends it as the next user message verbatim. No answer body, no
cards, no trust footer (nothing to verify yet).

### 4.4 Assistant message — fallback / low confidence
When the pipeline degrades to its safe fallback (`06` FR4, `confidence = 0.0`), show a distinct
neutral notice ("Couldn't produce a reliable answer for that") — never a normal answer block sitting
next to a suspiciously empty confidence meter.

## 5. Composer & ambient controls

### 5.1 Text input
Multiline, auto-grow. Placeholder is a real example query ("Why did revenue drop last week?"), not
a generic "type your message."

### 5.2 Source-scope selector (`07` FR3)
A three-way segmented control — `Your data` / `Live web` / `Both` — directly beside the input,
always visible, defaults to `Your data`, and persists across queries until changed (`07` FR2/FR3).
If a typed query implies external context while the selector is still on `Your data`, don't switch
it silently — that's what the clarification message type (§4.3) is for (`07` FR4).

### 5.3 Upload
Icon button, visible only when `plan !== "guest"` — not shown disabled, not shown at all (`04`
FR1). Opens a small popover: drag-drop zone, accepted-types hint ("CSV, PDF, or XLSX"), a size-limit
hint reflecting the real plan cap (3MB free / 10MB pro, `04` FR2). Uploaded files list below with a
status chip (`processing` / `completed` / `failed`); a `failed` chip surfaces the stored reason once
that field exists server-side (`04` edge case 1).

### 5.4 Send button
Disabled only when the input is empty — never disabled purely because quota is exhausted. Let the
send happen and surface the 429 as a proper message (§5.6); a pre-emptively disabled button can't
explain why it's disabled.

### 5.5 Quota chip (`02`)
Small, always visible near the composer: `"3 of 4 left · resets in 4h"`, sourced from the rolling
window (`02` FR1) — not a modal, not a settings-page-only stat.

### 5.6 The two 429 states look different (`02` FR5)
- **Window exhausted** (`contact_form` absent) — an inline notice in the message stream with the
  reset time; input stays enabled for the next window.
- **Lifetime cap reached** (`contact_form: true`) — a distinct, more permanent-feeling card, not a
  toast, with the contact form (name/email/message, `02` FR5) inline. This is a different emotional
  register from "come back later" and should read as one: it's a ceiling, not a rate limit.

### 5.7 Cold start / first load
Not a bare spinner. A named state — "Waking up the server — first load can take up to a minute" —
with a determinate-feeling progress element, shown only on a session's first request, distinct from
the normal per-message thinking indicator.

## 6. States not covered above

| State | Design note |
|---|---|
| Empty thread, guest | Invite a question directly; no upload affordance at all (§5.3). |
| Empty thread, registered, no files uploaded | Invite an upload first ("Add a CSV, PDF, or spreadsheet to get started"); a question with no data yet should probably route through the same "no data" messaging as `07` edge case 2, not a generic empty state. |
| Assistant "thinking" | A small inline indicator under the user's message — distinct from §5.7's first-load state, not a full-page block. |
| Auth-gated route, unauthenticated | Out of scope here — see `01-authentication.md` for the auth screens; this spec starts at the authenticated workspace. |

## 7. Visual language (tokens)

Deliberately framework-agnostic — `Frontend/CLAUDE.md` §4 notes the CSS approach isn't chosen yet.
Whatever's picked should resolve to these roles:

| Token | Purpose | Note |
|---|---|---|
| `surface-page` / `surface-card` / `surface-raised` | Three elevation steps | Visual-output cards sit one step above the page; popovers (upload, account menu) one step above that. |
| `text-primary` / `text-secondary` / `text-muted` | Body / supporting / captions | The insights strip and trust footer use `secondary`/`muted`, never `primary` — they support the answer, they aren't the answer. |
| `accent` | Source-scope active state, links, send action | One accent color, used sparingly. |
| `success` / `danger` / `warning` | Confidence bands, quota chip nearing zero, flagged-answer state | Must stay visually distinct from plain `accent` — these carry meaning. |
| type scale | 13px captions (quota chip, timestamps) / 15px body / 20–24px metric numbers | Nothing below 12px — the quota chip and trust footer get read often, in passing. |

Exact hex/HSL values and spacing scale are a follow-up once a CSS approach is chosen — this table
defines the roles that need values, not the values themselves.

## 8. Constraints

- **No third-party generative-UI SaaS** (Tambo or otherwise). `13-frontend-migration.md`'s reasoning
  for removing it from the other project — leaked-key risk, vendor lock-in on a pattern that's easy
  to own outright — applies equally here. The backend already returns `visual_type` (`06` FR3), so
  the frontend only needs a plain type-to-component lookup, no interception layer.
- **`visual_type`'s 7 values must match `06`'s `Literal[...]` exactly.** Known drift to fix first:
  `Frontend/docs/type-contracts.md`'s Chat section still lists the old 9-type enum (`line_chart`,
  `kpi_card`, etc.). Update that file to the 7 real types (`metric`, `graph`, `table`, `comparison`,
  `insight`, `alert`, `status`) before writing per-type renderers against it.
- **Confidence display is blocked on the backend schema bound** (`06` §3 / edge case 4) — build the
  meter component now, but gate its visibility on "is this value in a sane range," not on the
  field's mere presence.
- **No fullscreen or resizable-panel affordance at all** (§3). That complexity existed in the
  prior-art project to compensate for a separated layout; it has no reason to exist once cards
  render inline with their question.

## 9. Open questions (not blocking a first build)

1. Chat history rail: full transcript list, or grouped by day/week? Not specified anywhere yet.
2. Does a `graph` card need per-chart-type (line/bar/pie) visual treatment, or one consistent
   container regardless of the underlying chart? `06`/`type-contracts.md` don't pin `chart_data`'s
   shape down yet.
3. Account menu contents (plan, logout, contact-us) have no owning spec yet — possibly a short
   follow-up file, or fold into this one's next revision.

## 10. Acceptance criteria

- [ ] Every assistant answer with `visuals.length > 0` renders those cards inline in that message,
      never in a separate panel.
- [ ] "Show the query" and a confidence indicator are present on every non-fallback,
      non-clarification assistant message.
- [ ] A `clarification` response renders as a visually distinct message type with tappable options,
      never as a plain text bubble.
- [ ] The source-scope selector defaults to `Your data`, persists across queries in a session, and
      stays visible at all times next to the composer.
- [ ] Upload UI is entirely absent — not disabled — for guest-plan users.
- [ ] Window-exhausted and lifetime-cap 429s render as two visually distinct states, matching `02`
      FR5's `contact_form` flag.
- [ ] The first request of a session shows the named cold-start state (§5.7), not a bare spinner.
- [ ] `Frontend/docs/type-contracts.md`'s Chat section is updated to the 7 real `visual_type`
      values before any per-type renderer is built against it.

---

**Note on Figma access:** this draft was written without Dev Mode MCP access — every attempt this
session returned "Dev Mode MCP Server not enabled" from the Figma desktop app, even after being told
it was connected. To reconcile this spec against the actual Figma frames: confirm the Figma desktop
app (not browser) has this file open, Dev Mode MCP is on (Figma menu → Preferences → Enable Dev Mode
MCP Server), and the Claude desktop app has been restarted since enabling it — then ask for a
re-check.
