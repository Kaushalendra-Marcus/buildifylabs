/**
 * File-upload API — live backend (`app/routes/files.py`). Multipart upload
 * returns 202 with a `FileResponse`; listing is own-only server-side.
 */
import { http } from '../lib/http';
import type { FileResponse } from '../types';

export function uploadFile(file: File): Promise<FileResponse> {
  const form = new FormData();
  form.append('file', file);
  return http.post<FileResponse>('/files/upload', form, { isFormData: true });
}

export function listFiles(): Promise<FileResponse[]> {
  return http.get<FileResponse[]>('/files');
}

export function getFile(id: string): Promise<FileResponse> {
  return http.get<FileResponse>(`/files/${id}`);
}
