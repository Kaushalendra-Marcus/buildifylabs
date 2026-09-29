/**
 * DocumentPicker — the PDF multi-select for the chat composer ("Your data"
 * / "Both" scopes only). Lists the user's completed PDF uploads (GET
 * /files, live); toggling chips narrows document evidence to the picked
 * files (`file_ids` on the chat request). Nothing picked = all PDFs, the
 * long-standing default. Renders nothing for guests (no uploads), for the
 * Live web scope (no document evidence there), while loading, or when the
 * user has no completed PDFs.
 */
import { useEffect, useState } from 'react';
import { listFiles } from '../../api/files';
import type { FileResponse } from '../../types/upload';
import { useAuth } from '../../hooks/useAuth';
import { useDocFilterStore } from './doc-filter-store';
import { useScopeStore } from './scope-store';
import './document-picker.css';

function isPickable(file: FileResponse): boolean {
  return file.file_type === 'application/pdf' && file.status === 'completed';
}

export function DocumentPicker() {
  const { user } = useAuth();
  const scope = useScopeStore((state) => state.scope);
  const selectedFileIds = useDocFilterStore((state) => state.selectedFileIds);
  const toggleFile = useDocFilterStore((state) => state.toggleFile);
  const clearSelection = useDocFilterStore((state) => state.clearSelection);
  const pruneTo = useDocFilterStore((state) => state.pruneTo);

  const [files, setFiles] = useState<FileResponse[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Defensive: a failed file list hides the picker instead of ever
    // breaking the composer it renders inside.
    Promise.resolve()
      .then(() => listFiles())
      .then((loaded) => {
        if (cancelled) return;
        const list = loaded ?? [];
        setFiles(list);
        pruneTo(list.map((file) => file.id));
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [pruneTo]);

  if (user?.plan === 'guest') return null;
  if (scope !== 'own_data' && scope !== 'both') return null;
  if (files === null) return null;

  const pickable = files.filter(isPickable);
  if (pickable.length === 0) return null;

  const filtered = selectedFileIds.length > 0;

  return (
    <div className="document-picker" role="group" aria-label="Ask from selected documents">
      <span className="document-picker__label" title="Pick PDFs to answer from. Nothing picked means all PDFs.">
        {filtered
          ? `Documents (${selectedFileIds.length}/${pickable.length})`
          : 'Documents: all'}
      </span>
      <div className="document-picker__chips">
        {pickable.map((file) => (
          <button
            key={file.id}
            type="button"
            className="document-picker__chip"
            aria-pressed={selectedFileIds.includes(file.id)}
            title={file.file_name}
            onClick={() => toggleFile(file.id)}
          >
            {file.file_name}
          </button>
        ))}
        {filtered && (
          <button
            type="button"
            className="document-picker__reset"
            onClick={clearSelection}
          >
            All
          </button>
        )}
      </div>
    </div>
  );
}
