/**
 * Chat message store (Zustand) — the source of truth for the message stream
 * (specs/14 §4). The message types live here as a discriminated union:
 *   - user (4.1): right-aligned bubble + optional uploaded-file chip above
 *   - assistant:kind="answer" (4.2): structured answer block
 *   - assistant:kind="clarification" (4.3): quick-pick prompt
 *   - assistant:kind="fallback" (4.4): degraded neutral notice
 *   - system (F5, §5.6): the two quota 429 states — window-exhausted inline
 *     notice (with reset time) and lifetime-cap permanent card (contact form)
 *     — plus a generic send-error notice. `window-exhausted` is transient
 *     (input stays enabled); `lifetime-cap` reads as a ceiling, not a pause.
 *
 * Assistant messages carry the raw `PipelineOutput`; `classifyOutput`
 * decides at render time which of the three assistant kinds it is — a
 * `clarification` response is never treated as an answer, and a degraded
 * fallback (empty visuals + confidence 0) is never shown as a normal answer
 * block next to an empty confidence meter (specs/14 §4.4).
 *
 * The store also carries two high-level UI states: `activeFileName` (the file
 * chip shown above a user message, 4.1 — set by the upload flow) and
 * `pending` (the in-flight indicator during send; `cold-start` only on a
 * session's first request, §5.7, otherwise `thinking` — F6 renders it).
 */
import { create } from 'zustand';
import type { PipelineOutput } from '../../types/chat';

export type PendingKind = 'cold-start' | 'thinking';

export interface UserChatMessage {
  id: string;
  role: 'user';
  content: string;
  /** Uploaded-file chip rendered ABOVE the bubble (4.1) — F5 fills it. */
  fileName?: string | null;
}

export interface AssistantChatMessage {
  id: string;
  role: 'assistant';
  output: PipelineOutput;
}

export type SystemNoticeKind = 'window-exhausted' | 'lifetime-cap' | 'error';

export interface SystemChatMessage {
  id: string;
  role: 'system';
  kind: SystemNoticeKind;
  /** Epoch ms when the window rolls over (window-exhausted) — the notice
   *  renders a live countdown from this. Null ⇒ unknown (fall back to
   *  "shortly"). */
  resetAt?: number | null;
  /** Display text for the transient error notice. */
  text?: string | null;
}

export type ChatMessage =
  | UserChatMessage
  | AssistantChatMessage
  | SystemChatMessage;

export type AssistantMessageKind =
  | 'answer'
  | 'clarification'
  | 'fallback';

/** Map a `PipelineOutput` to the assistant message kind it renders as.
 *  A non-null `clarification` always wins (quick-pick prompt, never an
 *  answer block); otherwise an empty-visuals + confidence 0 pipeline is the
 *  degraded fallback; everything else is a normal answer (specs/14 §4.2–4.4). */
export function classifyAssistantOutput(
  output: PipelineOutput,
): AssistantMessageKind {
  if (output.clarification) return 'clarification';
  if (output.visuals.length === 0 && output.confidence === 0) return 'fallback';
  return 'answer';
}

interface ChatState {
  messages: ChatMessage[];
  /** In-flight send indicator (F5/F6): `cold-start` on a session's first
   *  request (§5.7), `thinking` otherwise (F6 renders it). */
  pending: PendingKind | null;
  /** Name of the latest completed upload — the file chip above user bubbles. */
  activeFileName: string | null;
  /** Whether the user has at least one completed data file (F6, specs/14 §6):
   *  `null` = unknown (not yet checked), `true` = has data, `false` = none.
   *  Drives the empty-thread invite (registered + no files → invite an upload)
   *  and routes a no-data question through the no-data messaging (07 edge case
   *  2) instead of a generic empty. */
  hasData: boolean | null;
  addUserMessage(content: string, fileName?: string | null): void;
  addAssistantMessage(output: PipelineOutput): void;
  addSystemNotice(kind: SystemNoticeKind, resetAt?: number | null, text?: string | null): void;
  setPending(pending: PendingKind | null): void;
  setActiveFileName(fileName: string | null): void;
  setHasData(hasData: boolean | null): void;
  clearChat(): void;
}

let nextId = 0;
function makeId(): string {
  nextId += 1;
  return `m-${nextId}`;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  pending: null,
  activeFileName: null,
  hasData: null,

addUserMessage: (content, fileName = null) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), role: 'user', content, fileName },
      ],
    })),

  addAssistantMessage: (output) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), role: 'assistant', output },
      ],
    })),

  addSystemNotice: (kind, resetAt = null, text = null) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), role: 'system', kind, resetAt, text },
      ],
    })),

  setPending: (pending) => set({ pending }),

  setActiveFileName: (fileName) => set({ activeFileName: fileName }),

  setHasData: (hasData) => set({ hasData }),

  clearChat: () => set({ messages: [], pending: null }),
}));