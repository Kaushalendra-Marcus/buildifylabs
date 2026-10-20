/**
 * File-upload API — live backend (`app/routes/files.py`). Multipart upload
 * returns 202 with a `FileResponse`; listing is own-only server-side.
 */
import { http } from '../lib/http';
import type { FilePreview, FileResponse } from '../types';

export function uploadFile(file: File): Promise<FileResponse> {
  const form = new FormData();
  form.append('file', file);
  // Upload + parse can legitimately exceed the default 30s for a 10 MB file
  // on a slow connection; keep the generic timeout for ordinary JSON calls.
  return http.post<FileResponse>('/files/upload', form, {
    isFormData: true,
    timeoutMs: 120_000,
  });
}

export function listFiles(): Promise<FileResponse[]> {
  return http.get<FileResponse[]>('/files');
}

export function getFile(id: string): Promise<FileResponse> {
  return http.get<FileResponse>(`/files/${id}`);
}

export function previewFile(id: string): Promise<FilePreview> {
  return http.get<FilePreview>(`/files/${id}/preview`);
}

export function deleteFile(id: string): Promise<void> {
  return http.del<void>(`/files/${id}`);
}
