/**
 * doc-filter-store tests — toggle/clear/prune, the chat document picker's
 * selection state. Empty = all PDFs (backend default).
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useDocFilterStore } from './doc-filter-store'

beforeEach(() => {
  localStorage.clear()
  useDocFilterStore.setState({ selectedFileIds: [] })
})

describe('doc-filter-store', () => {
  it('toggling adds then removes an id', () => {
    useDocFilterStore.getState().toggleFile('a')
    expect(useDocFilterStore.getState().selectedFileIds).toEqual(['a'])
    useDocFilterStore.getState().toggleFile('a')
    expect(useDocFilterStore.getState().selectedFileIds).toEqual([])
  })

  it('holds multiple ids and clears them', () => {
    useDocFilterStore.getState().toggleFile('a')
    useDocFilterStore.getState().toggleFile('b')
    expect(useDocFilterStore.getState().selectedFileIds).toEqual(['a', 'b'])
    useDocFilterStore.getState().clearSelection()
    expect(useDocFilterStore.getState().selectedFileIds).toEqual([])
  })

  it('prunes ids of files that no longer exist', () => {
    useDocFilterStore.setState({ selectedFileIds: ['a', 'gone'] })
    useDocFilterStore.getState().pruneTo(['a'])
    expect(useDocFilterStore.getState().selectedFileIds).toEqual(['a'])
  })
})
