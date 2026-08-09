import { describe, expect, it } from 'vitest'
import { VISUAL_TYPES, isVisualType } from './visuals'

describe('visuals contract (F0 type freeze)', () => {
  it('exposes exactly the 7 real visual types in order', () => {
    expect(VISUAL_TYPES).toEqual([
      'metric',
      'graph',
      'table',
      'comparison',
      'insight',
      'alert',
      'status',
    ])
  })

  it('has a working isVisualType runtime guard', () => {
    for (const type of VISUAL_TYPES) {
      expect(isVisualType(type)).toBe(true)
    }
    // Old 9-type enum values from the pre-B4 contract must be rejected.
    expect(isVisualType('line_chart')).toBe(false)
    expect(isVisualType('kpi_card')).toBe(false)
    expect(isVisualType('bar_chart')).toBe(false)
    expect(isVisualType('india_map')).toBe(false)
    // Non-strings.
    expect(isVisualType(null)).toBe(false)
    expect(isVisualType(undefined)).toBe(false)
    expect(isVisualType(42)).toBe(false)
    expect(isVisualType({})).toBe(false)
  })
})
