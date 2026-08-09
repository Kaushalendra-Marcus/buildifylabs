/**
 * UploadPopover (F5, specs/14 §5.3 / specs/04) — the small popover opened by
 * the composer's upload button (which is ABSENT for `guest` plans, never shown
 * disabled). Drag-drop or browse accept "CSV, PDF, or XLSX"; the size hint
 * reflects the real plan cap (3MB free / 10MB pro, specs/04 FR2). The user's
 * files list below with a status chip (`processing`/`completed`/`failed`) and
 * a `failed` chip surfaces the stored reason (specs/04 edge case 1).
 *
 * The active (most recent) upload name becomes the file chip above the next
 * user message (specs/14 §4.1) via `setActiveFileName`.
 */
import { FileText, UploadCloud } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { listFiles, uploadFile } from '../../api/files';
import { useAuth } from '../../hooks/useAuth';
import { getErrorMessage } from '../../lib/errors';
import type { FileResponse, FileStatus } from '../../types';
import { useChatStore } from './chat-store';

const STATUS_LABEL: Record<FileStatus, string> = {
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
};

export function UploadPopover({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const isPro = user?.plan === 'pro';
  const sizeLimit = isPro ? '10 MB' : '3 MB';

  const [files, setFiles] = useState<FileResponse[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const loadFiles = useCallback(async () => {
    try {
      setFiles(await listFiles());
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  }, []);

  useEffect(() => {
    void loadFiles();
  }, [loadFiles]);

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      try {
        const created = await uploadFile(file);
        // The most recent upload names the chip on the next user bubble, and
        // the user now has data (F6: no-data messaging / empty-thread invite).
        useChatStore.getState().setActiveFileName(created.file_name);
        useChatStore.getState().setHasData(true);
        await loadFiles(); // refresh status chips (processing → completed/failed)
      } catch (caught) {
        setError(getErrorMessage(caught));
      } finally {
        setUploading(false);
      }
    },
    [loadFiles],
  );

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void upload(file);
  }

  function handlePick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void upload(file);
    event.target.value = ''; // allow re-selecting the same file
  }

  return (
    <div className="upload-popover" role="dialog" aria-label="Upload files">
      <p className="upload-popover__title">Upload data</p>

      <div
        className="upload-popover__dropzone"
        data-dragging={dragging ? 'true' : 'false'}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <UploadCloud size={22} aria-hidden="true" />
        <span className="upload-popover__dropzone-text">
          Drag and drop a file here, or
        </span>
        <button
          type="button"
          className="upload-popover__browse"
          onClick={() => inputRef.current?.click()}
        >
          Browse
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.pdf,.xlsx"
          className="upload-popover__file-input"
          onChange={handlePick}
          aria-hidden="true"
          tabIndex={-1}
        />
      </div>

      <p className="upload-popover__hints">CSV, PDF, or XLSX · {sizeLimit} max</p>

      {uploading && (
        <p className="upload-popover__uploading" role="status">
          Uploading…
        </p>
      )}
      {error && (
        <p className="upload-popover__error" role="alert">
          {error}
        </p>
      )}

      {files.length > 0 && (
        <ul className="upload-popover__list">
          {files.map((file) => (
            <li key={file.id} className="upload-popover__item">
              <FileText size={14} aria-hidden="true" />
              <span className="upload-popover__name">{file.file_name}</span>
              <span
                className="upload-popover__status"
                data-status={file.status}
              >
                {STATUS_LABEL[file.status]}
              </span>
              {file.status === 'failed' && file.error && (
                <span className="upload-popover__reason">{file.error}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <button type="button" className="upload-popover__close" onClick={onClose}>
        Done
      </button>
    </div>
  );
}