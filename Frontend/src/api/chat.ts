/**
 * Chat API — live backend (`app/routes/chat.py`). `sendQuery` is the single
 * entry point for the message stream; `flagAnswer` feeds the trust footer
 * (specs/10 §2). Swap seam: components never know the endpoint shape.
 *
 * `sendQuery` reads the SSE answer stream (`POST /chat/stream`) so callers
 * get live stage callbacks plus incremental answer-text deltas while
 * waiting; the resolved value is the same `PipelineOutput` as the unary
 * endpoint, and quota 429s surface as the same `ApiError` the composer
 * already handles.
 */
import { ApiError, apiUrl, authHeaders, http } from '../lib/http';
import type { ChatRequest, FlagRequest, FlagResponse, PipelineOutput } from '../types';

export type QueryStageHandler = (stage: string) => void;
export type QueryTextHandler = (delta: string) => void;

interface StreamEvent {
  stage?: string;
  text?: string;
  result?: PipelineOutput;
  error?: string;
}

async function readStream(
  response: Response,
  onStage?: QueryStageHandler,
  onText?: QueryTextHandler,
): Promise<PipelineOutput> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Streaming is not supported in this browser.');
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const line = frame.split('\n').find((part) => part.startsWith('data: '));
      if (!line) continue;
      let event: StreamEvent;
      try {
        event = JSON.parse(line.slice('data: '.length)) as StreamEvent;
      } catch {
        continue;
      }
      if (event.stage && onStage) onStage(event.stage);
      if (typeof event.text === 'string' && event.text && onText) onText(event.text);
      if (event.error) throw new Error(event.error);
      if (event.result) {
        await reader.cancel().catch(() => undefined);
        return event.result;
      }
    }
  }
  throw new Error('The answer stream ended before a result arrived.');
}

export async function sendQuery(
  body: ChatRequest,
  onStage?: QueryStageHandler,
  onText?: QueryTextHandler,
): Promise<PipelineOutput> {
  const headers = authHeaders();
  headers.set('Content-Type', 'application/json');
  const response = await fetch(apiUrl('/chat/stream'), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // Non-JSON error body — leave null.
    }
    throw new ApiError(response.status, errorBody);
  }

  return readStream(response, onStage, onText);
}

export function flagAnswer(body: FlagRequest): Promise<FlagResponse> {
  return http.post<FlagResponse>('/chat/flag', body);
}
