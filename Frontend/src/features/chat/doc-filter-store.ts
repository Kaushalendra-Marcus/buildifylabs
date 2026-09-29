/**
 * Document-filter store — the chat document picker ("Your data" / "Both"
 * scopes only). Holds the picked PDF upload ids; empty means "all PDFs"
 * (backend default, existing behavior). Persists across reloads like the
 * scope selector, and is only ever changed by the user toggling chips —
 * never silently. Stale ids (deleted files) are pruned when the file list
 * loads.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface DocFilterState {
  selectedFileIds: string[];
  toggleFile(id: string): void;
  clearSelection(): void;
  pruneTo(existingIds: string[]): void;
}

export const useDocFilterStore = create<DocFilterState>()(
  persist(
    (set) => ({
      selectedFileIds: [],
      toggleFile: (id) =>
        set((state) =>
          state.selectedFileIds.includes(id)
            ? { selectedFileIds: state.selectedFileIds.filter((item) => item !== id) }
            : { selectedFileIds: [...state.selectedFileIds, id] },
        ),
      clearSelection: () => set({ selectedFileIds: [] }),
      pruneTo: (existingIds) =>
        set((state) => ({
          selectedFileIds: state.selectedFileIds.filter((id) => existingIds.includes(id)),
        })),
    }),
    { name: 'buildifylabs.doc-filter' },
  ),
);
