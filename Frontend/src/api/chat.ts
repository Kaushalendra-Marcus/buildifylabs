/**
 * Chat API — live backend (`app/routes/chat.py`). `sendQuery` is the single
 * entry point for the message stream; `flagAnswer` feeds the trust footer
 * (specs/10 §2). Swap seam: components never know the endpoint shape.
 */
import { http } from '../lib/http';
import type { ChatRequest, FlagRequest, FlagResponse, PipelineOutput } from '../types';

export function sendQuery(body: ChatRequest): Promise<PipelineOutput> {
  return http.post<PipelineOutput>('/chat', body);
}

export function flagAnswer(body: FlagRequest): Promise<FlagResponse> {
  return http.post<FlagResponse>('/chat/flag', body);
}
