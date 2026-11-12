import { describe, expect, it } from 'vitest'
import { SEARCH_NUM_DEFAULT, clampSearchNum } from '.'

describe('clampSearchNum', () => {
  it('keeps whole numbers within 1–20 and rounds the rest into range', () => {
    expect([1, 7, 20].map(clampSearchNum)).toEqual([1, 7, 20])
    expect([0, -3, 21, 100, 2.4, 2.6].map(clampSearchNum)).toEqual([1, 1, 20, 20, 2, 3])
  })

  it('falls back to the default (the server DOC_COUNT) for non-numbers', () => {
    expect(SEARCH_NUM_DEFAULT).toBe(20)
    expect([Number.NaN, Number.POSITIVE_INFINITY].map(clampSearchNum)).toEqual([SEARCH_NUM_DEFAULT, SEARCH_NUM_DEFAULT])
  })
})
