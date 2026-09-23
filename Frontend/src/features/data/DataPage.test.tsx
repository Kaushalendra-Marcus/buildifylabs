import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DataPage } from './DataPage';
import { useAuthStore } from '../auth/auth-store';
import { deleteFile, listFiles, previewFile } from '../../api/files';

vi.mock('../../api/files', () => ({
  listFiles: vi.fn().mockResolvedValue([
    {
      id: 'file-1',
      file_name: 'sales.csv',
      file_type: 'csv',
      file_size: 1234,
      status: 'completed',
      pinecone_namespace: 'user_table_1',
      error: null,
      created_at: new Date('2026-01-02T10:00:00Z').toISOString(),
    },
  ]),
  uploadFile: vi.fn(),
  previewFile: vi.fn().mockResolvedValue({
    file_id: 'file-1',
    file_name: 'sales.csv',
    kind: 'table',
    columns: ['revenue', 'region'],
    rows: [{ revenue: 100, region: 'east' }],
  }),
  deleteFile: vi.fn().mockResolvedValue(undefined),
}));

function renderData() {
  return render(
    <MemoryRouter>
      <DataPage />
    </MemoryRouter>,
  );
}

describe('DataPage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.getState().signOut();
    useAuthStore.getState().setSession({
      user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
    });
  });

  it('lists datasets with status chips', async () => {
    renderData();
    expect(await screen.findByText('sales.csv')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('shows the upload dropzone for non-guest plans', async () => {
    renderData();
    await screen.findByText('sales.csv');
    expect(screen.getByLabelText('Upload a data file')).toBeInTheDocument();
  });

  it('opens a preview dialog for a file', async () => {
    const user = userEvent.setup();
    renderData();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByRole('dialog', { name: 'Preview of sales.csv' })).toBeInTheDocument();
    expect(previewFile).toHaveBeenCalledWith('file-1');
    expect(screen.getByText('revenue')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(screen.queryByRole('dialog', { name: 'Preview of sales.csv' })).toBeNull();
  });

  it('deletes a file after a two-step confirm', async () => {
    const user = userEvent.setup();
    renderData();
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    await user.click(await screen.findByRole('button', { name: 'Confirm delete' }));
    expect(deleteFile).toHaveBeenCalledWith('file-1');
    expect(listFiles).toHaveBeenCalled();
  });
});
