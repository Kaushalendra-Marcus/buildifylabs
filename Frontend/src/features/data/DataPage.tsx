/**
 * DataPage — the Datasets page (`/app/data`). Same live seam as the
 * UploadPopover (listFiles/uploadFile) but as a full page: file table with
 * status chips + failure reason, storage-limit hint, drag-drop upload.
 * No delete affordance yet (no backend route) — replace works by uploading
 * a new CSV/XLSX, which the backend lands in the per-user table.
 */
import { FileText, RefreshCw, UploadCloud } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { listFiles, uploadFile } from '../../api/files';
import { useAuth } from '../../hooks/useAuth';
import { getErrorMessage } from '../../lib/errors';
import type { FileResponse, FileStatus } from '../../types';
import { useChatStore } from '../chat/chat-store';
import './data.css';

const STATUS_LABEL: Record<FileStatus, string> = {
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
};

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function DataPage() {
  const { user } = useAuth();
  const isGuest = user?.plan === 'guest';
  const isPro = user?.plan === 'pro';
  const sizeLimit = isPro ? '10 MB' : '3 MB';

  const [files, setFiles] = useState<FileResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setFiles(await listFiles());
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setLoading(false);
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
        useChatStore.getState().setActiveFileName(created.file_name);
        useChatStore.getState().setHasData(true);
        setFiles(await listFiles());
      } catch (caught) {
        setError(getErrorMessage(caught));
      } finally {
        setUploading(false);
      }
    },
    [],
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
    event.target.value = '';
  }

  return (
    <div className="data-page" role="main" aria-label="Datasets">
      <section className="data-page__head">
        <div>
          <p className="data-page__eyebrow">Data</p>
          <h1 className="data-page__title">Datasets</h1>
          <p className="data-page__sub">
            CSV and XLSX land in your queryable table (a new upload replaces
            it). PDFs are chunked for document answers. Limit: {sizeLimit}.
          </p>
        </div>
        <button
          type="button"
          className="data-page__refresh"
          onClick={() => void loadFiles()}
          disabled={loading}
        >
          <RefreshCw size={15} aria-hidden="true" />
          Refresh
        </button>
      </section>

      {!isGuest && (
        <div
          className="data-page__dropzone"
          data-dragging={dragging ? 'true' : 'false'}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          <UploadCloud size={22} aria-hidden="true" />
          <span>Drag and drop a file here, or</span>
          <button
            type="button"
            className="data-page__browse"
            onClick={() => inputRef.current?.click()}
          >
            Browse
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.pdf,.xlsx"
            className="data-page__file-input"
            onChange={handlePick}
            aria-label="Upload a data file"
          />
          <span className="data-page__hint">CSV, PDF, or XLSX · {sizeLimit} max</span>
        </div>
      )}
      {isGuest && (
        <p className="data-page__notice" role="note">
          Guest plans can ask questions but cannot upload files. Sign up for a
          free account to add your own data.
        </p>
      )}

      {uploading && (
        <p className="data-page__status" role="status">
          Uploading…
        </p>
      )}
      {error && (
        <p className="data-page__error" role="alert">
          {error}
        </p>
      )}

      <section className="data-page__table-wrap" aria-label="Uploaded files">
        {loading ? (
          <p className="data-page__status">Loading datasets…</p>
        ) : files.length === 0 ? (
          <p className="data-page__empty">
            No datasets yet. Upload a CSV to ask your first question.
          </p>
        ) : (
          <table className="data-page__table">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Type</th>
                <th scope="col">Size</th>
                <th scope="col">Status</th>
                <th scope="col">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={file.id}>
                  <td>
                    <span className="data-page__file">
                      <FileText size={14} aria-hidden="true" />
                      {file.file_name}
                    </span>
                    {file.status === 'failed' && file.error && (
                      <span className="data-page__reason">{file.error}</span>
                    )}
                  </td>
                  <td>{file.file_type}</td>
                  <td>{formatSize(file.file_size)}</td>
                  <td>
                    <span className="data-page__status-chip" data-status={file.status}>
                      {STATUS_LABEL[file.status]}
                    </span>
                  </td>
                  <td>{formatDate(file.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
