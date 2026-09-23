/**
 * Reports store — pinned charts ("Pin to reports" under any chat answer).
 * Frontend-only v1 (zustand persist, same pattern as chat-store): no backend
 * change needed. A future backend can mirror these off `query_log_id`.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { PipelineOutput } from '../../types/chat';

export interface PinnedReport {
  id: string;
  title: string;
  answer: string;
  output: PipelineOutput;
  conversationId: string;
  pinnedAt: number;
}

interface ReportsState {
  reports: PinnedReport[];
  pinReport(output: PipelineOutput, conversationId: string): void;
  unpinReport(id: string): void;
  isPinned(queryLogId: string | null): boolean;
  clearReports(): void;
}

let nextReportId = 0;
function makeReportId(): string {
  nextReportId += 1;
  return `r-${Date.now()}-${nextReportId}`;
}

function reportKey(output: PipelineOutput, conversationId: string): string {
  return `${conversationId}:${output.query_log_id ?? output.answer.slice(0, 64)}`;
}

export const useReportsStore = create<ReportsState>()(
  persist(
    (set, get) => ({
      reports: [],

      pinReport: (output, conversationId) => {
        const key = reportKey(output, conversationId);
        const exists = get().reports.some(
          (r) => reportKey(r.output, r.conversationId) === key,
        );
        if (exists) return;
        const title =
          output.visuals[0]?.title ??
          output.answer.slice(0, 48).trim() ??
          'Pinned answer';
        set((state) => ({
          reports: [
            {
              id: makeReportId(),
              title,
              answer: output.answer,
              output,
              conversationId,
              pinnedAt: Date.now(),
            },
            ...state.reports,
          ].slice(0, 50),
        }));
      },

      unpinReport: (id) =>
        set((state) => ({
          reports: state.reports.filter((r) => r.id !== id),
        })),

      isPinned: (queryLogId) => {
        if (!queryLogId) return false;
        return get().reports.some((r) => r.output.query_log_id === queryLogId);
      },

      clearReports: () => set({ reports: [] }),
    }),
    {
      name: 'buildifylabs-reports',
      version: 1,
      partialize: (state) => ({ reports: state.reports.slice(0, 50) }),
    },
  ),
);
