/**
 * Part B — per-conversation transcripts: every message carries the
 * conversation id it belongs to, and `messagesForConversation` restores
 * exactly one thread for the history rail.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { messagesForConversation, useChatStore } from './chat-store'
import type { PipelineOutput } from '../../types/chat'

function makeOutput(answer = 'an answer'): PipelineOutput {
  return {
    answer,
    visuals: [],
    insights: [],
    summary: '',
    root_causes: [],
    recommendations: [],
    news_context: [],
    anomalies: [],
    confidence: 0.8,
    clarification: null,
    sql_query: 'SELECT 1;',
    data_preview: [],
    query_log_id: 'log-1',
  }
}

beforeEach(() => {
  localStorage.clear()
  useChatStore.getState().clearChat()
})

describe('chat-store per-conversation transcripts (Part B)', () => {
  it('keeps two conversations separate and restores each by id', () => {
    useChatStore.getState().addUserMessage('first question in A')
    useChatStore.getState().addAssistantMessage(makeOutput('answer in A'))
    const conversationA = useChatStore.getState().activeConversationId
    expect(conversationA).toBeTruthy()

    useChatStore.getState().newChat()
    useChatStore.getState().addUserMessage('only question in B')
    const conversationB = useChatStore.getState().activeConversationId
    expect(conversationB).toBeTruthy()
    expect(conversationB).not.toBe(conversationA)

    const { messages } = useChatStore.getState()
    const onlyA = messagesForConversation(messages, conversationA)
    expect(onlyA).toHaveLength(2)
    expect(onlyA.map((m) => m.id)).toEqual(messages.slice(0, 2).map((m) => m.id))

    const onlyB = messagesForConversation(messages, conversationB)
    expect(onlyB).toHaveLength(1)
    expect(onlyB[0].role).toBe('user')
  })

  it('a fresh new chat shows an empty stream, not the old history', () => {
    useChatStore.getState().addUserMessage('old question')
    useChatStore.getState().newChat()

    const { messages, activeConversationId } = useChatStore.getState()
    expect(activeConversationId).toBeTruthy()
    expect(messagesForConversation(messages, activeConversationId)).toHaveLength(0)
  })

  it('selecting a past conversation restores its messages', () => {
    useChatStore.getState().addUserMessage('question in A')
    const conversationA = useChatStore.getState().activeConversationId as string

    useChatStore.getState().newChat()
    useChatStore.getState().addUserMessage('question in B')

    useChatStore.getState().selectConversation(conversationA)
    const { messages, activeConversationId } = useChatStore.getState()
    const visible = messagesForConversation(messages, activeConversationId)
    expect(visible).toHaveLength(1)
    expect(visible[0].role).toBe('user')
    if (visible[0].role === 'user') {
      expect(visible[0].content).toBe('question in A')
    }
  })
})
