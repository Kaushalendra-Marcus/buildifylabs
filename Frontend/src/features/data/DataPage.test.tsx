import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DataPage } from './DataPage';
import { useAuthStore } from '../auth/auth-store';

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
});
