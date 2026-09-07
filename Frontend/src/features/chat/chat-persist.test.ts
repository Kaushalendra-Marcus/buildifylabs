/**
 * Chat thread persistence — the stream survives a reload via zustand/persist
 * (localStorage), capped so previews can't blow the quota. Transient send
 * state (pending/stage) never persists.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useChatStore } from './chat-store'

const STORAGE_KEY = 'buildifylabs-chat'

function storedMessages(): Array<{ content?: string }> {
  const raw = localStorage.getItem(STORAGE_KEY)
  expect(raw).toBeTruthy()
  const parsed = JSON.parse(raw as string) as {
    state: { messages: Array<{ content?: string }> }
  }
  return parsed.state.messages
}

describe('chat thread persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    useChatStore.getState().clearChat()
  })

  it('writes user messages to localStorage', () => {
    useChatStore.getState().addUserMessage('persist me?')

    expect(storedMessages().at(-1)).toMatchObject({ content: 'persist me?' })
  })

  it('never persists the in-flight pending state', () => {
    useChatStore.getState().addUserMessage('q?')
    useChatStore.getState().setPending('thinking')

    const raw = localStorage.getItem(STORAGE_KEY) as string
    expect(raw).not.toContain('thinking')
  })

  it('caps the persisted tail at 50 messages', () => {
    for (let index = 0; index < 60; index += 1) {
      useChatStore.getState().addUserMessage(`question ${index}`)
    }

    expect(storedMessages().length).toBeLessThanOrEqual(50)
    expect(useChatStore.getState().messages.length).toBe(60)
  })
})
