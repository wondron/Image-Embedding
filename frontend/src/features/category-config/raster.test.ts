import { expect, it } from 'vitest'
import { rasterScale } from './raster'

it('exports ordinary diagrams at double resolution and reduces larger ones to fit', () => {
  expect(rasterScale(720, 600)).toBe(2)
  expect(rasterScale(20_000, 100)).toBeCloseTo(0.8)
  expect(rasterScale(10_000, 10_000)).toBe(1)
})

it('keeps readable boundary sizes but refuses diagrams that require shrinking below half size', () => {
  expect(rasterScale(32_000, 100)).toBe(0.5)
  expect(rasterScale(20_000, 20_000)).toBe(0.5)
  expect(rasterScale(32_001, 100)).toBe(0)
  expect(rasterScale(20_001, 20_001)).toBe(0)
})

it('refuses invalid dimensions', () => {
  for (const [width, height] of [[0, 100], [100, -1], [Number.NaN, 100], [100, Number.POSITIVE_INFINITY]]) {
    expect(rasterScale(width, height)).toBe(0)
  }
})
