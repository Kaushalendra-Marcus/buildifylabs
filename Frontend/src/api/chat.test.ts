/**
 * sendQuery stream reader — parses the SSE answer stream with a stubbed
 * fetch (no network): stage events, the final result, error events, and
 * non-2xx quota bodies.
 */
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/http'
import { sendQuery } from './chat'

function sseResponse(frames: string[]): Response {
  const text = frames.map((frame) => `data: ${frame}\n\n`).join('')
  const bytes = new TextEncoder().encode(text)
  let sent = false
  const body = {
    getReader: () => ({
      read: async () => {
        if (sent) return { done: true, value: undefined }
        sent = true
        return { done: false, value: bytes }
      },
      cancel: async () => undefined,
    }),
  }
  return { ok: true, status: 200, body } as unknown as Response
}

const OUTPUT = {
  answer: 'Streamed answer.',
  visuals: [],
  insights: [],
  summary: '',
  root_causes: [],
  recommendations: [],
  news_context: [],
  anomalies: [],
  confidence: 0.7,
  clarification: null,
  sql_query: null,
  data_preview: null,
  query_log_id: 'log-9',
}

describe('sendQuery stream', () => {
  it('emits stages in order and resolves the final result', async () => {
    const stages: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          JSON.stringify({ stage: 'evidence' }),
          JSON.stringify({ stage: 'judging' }),
          JSON.stringify({ stage: 'narrating' }),
          JSON.stringify({ result: OUTPUT }),
        ]),
      ),
    )
    try {
      const output = await sendQuery(
        { query: 'how is revenue?' },
        (stage) => stages.push(stage),
      )
      expect(stages).toEqual(['evidence', 'judging', 'narrating'])
      expect(output).toEqual(OUTPUT)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('throws the stream error event as a plain error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([JSON.stringify({ error: 'Boom.' })]),
      ),
    )
    try {
      await expect(sendQuery({ query: 'q' })).rejects.toThrow('Boom.')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('throws quota 429s as ApiError like the unary endpoint', async () => {
    const body = { detail: 'Window exhausted.', contact_form: false }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: async () => body,
      }),
    )
    try {
      await expect(sendQuery({ query: 'q' })).rejects.toMatchObject({
        status: 429,
      })
      await sendQuery({ query: 'q' }).catch((error: unknown) => {
        expect(error).toBeInstanceOf(ApiError)
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('rejects when the stream ends with no result', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(sseResponse([JSON.stringify({ stage: 'x' })])),
    )
    try {
      await expect(sendQuery({ query: 'q' })).rejects.toThrow(
        'ended before a result',
      )
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
