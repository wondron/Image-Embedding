import { describe, expect, it } from 'vitest'
import {
  MAX_CLASSES, PALETTE, classColor, hexToRgb, maskValues, outlineRadius, paintCutout, paintMask, paintOutline, summarize,
} from './mask'
import { captionFrom, thresholdFrom, toDetectionResult, toSegmentResult } from './model'

/** A width × height mask from rows of class ids. */
function mask(rows: number[][]): Uint8Array {
  return Uint8Array.from(rows.flat())
}

/** The alpha of each pixel of an RGBA buffer, as rows. */
function alphas(rgba: Uint8ClampedArray, width: number): number[][] {
  const rows: number[][] = []
  for (let i = 0; i < rgba.length / 4; i += width) rows.push(Array.from({ length: width }, (_, x) => rgba[(i + x) * 4 + 3]))
  return rows
}

describe('model tools input', () => {
  it('joins the categories typed the way GroundingDINO separates them, blank means the configured caption', () => {
    expect(captionFrom('')).toBeNull()
    expect(captionFrom('  . , ')).toBeNull()
    expect(captionFrom('food')).toBe('food')
    expect(captionFrom('food . plate .')).toBe('food . plate')
    expect(captionFrom('food, plate，bowl、cup;lid\nspoon')).toBe('food . plate . bowl . cup . lid . spoon')
  })

  it('reads a threshold in 0–1; blank is the configured value, anything else is invalid', () => {
    expect(thresholdFrom('')).toBeNull()
    expect(thresholdFrom(' 0.35 ')).toBe(0.35)
    expect(thresholdFrom('0')).toBe(0)
    expect(thresholdFrom('1')).toBe(1)
    for (const text of ['1.2', '-0.1', 'abc', 'NaN']) expect(thresholdFrom(text)).toBeUndefined()
  })
})

describe('model tools responses', () => {
  const detection = {
    ok: true, filename: 'dish.png', width: 160, height: 90, caption: 'food . plate',
    box_threshold: 0.3, text_threshold: 0.25, latency_ms: 812.4,
    detections: [
      { label: 'plate', score: 0.52, box: [10, 5, 150, 85] },
      { label: 'food', score: 0.88, box: [40, 20, 120, 70] },
      { label: '', score: 0.4, box: [0, 0, 160, 90] },
    ],
  }

  it('reads a detection, highest score first, keeping a box without a matched word', () => {
    const result = toDetectionResult(detection)
    expect(result).toMatchObject({ width: 160, height: 90, caption: 'food . plate', boxThreshold: 0.3, textThreshold: 0.25, latencyMs: 812.4 })
    expect(result?.detections.map(item => [item.label, item.score])).toEqual([['food', 0.88], ['plate', 0.52], ['', 0.4]])
    expect(result?.detections[0].box).toEqual([40, 20, 120, 70])
  })

  it('refuses a detection body that is not one', () => {
    const broken = [
      { ...detection, ok: false },
      { ...detection, caption: undefined },
      { ...detection, box_threshold: 1.5 },
      { ...detection, width: 0 },
      { ...detection, detections: null },
      { ...detection, detections: [{ label: 'food', score: 0.9, box: [40, 20, 170, 70] }] }, // outside the image
      { ...detection, detections: [{ label: 'food', score: 0.9, box: [40, 20, 40, 70] }] }, // empty
      { ...detection, detections: [{ label: 'food', score: 1.2, box: [40, 20, 120, 70] }] },
      { ...detection, detections: ['food'] },
      null,
    ]
    for (const body of broken) expect(toDetectionResult(body)).toBeNull()
  })

  it('reads a segmentation as a data URL of its mask, and refuses one without a mask', () => {
    const body = { ok: true, filename: 'dish.png', width: 160, height: 90, latency_ms: 350, mask_png_base64: 'iVBORw0KGgo=', visual_png_base64: null }
    expect(toSegmentResult(body)).toEqual({ width: 160, height: 90, latencyMs: 350, maskUrl: 'data:image/png;base64,iVBORw0KGgo=' })
    for (const broken of [{ ...body, mask_png_base64: '' }, { ...body, mask_png_base64: 'not base64!' }, { ...body, height: -1 }, { ...body, ok: false }]) {
      expect(toSegmentResult(broken)).toBeNull()
    }
  })
})

describe('segmentation mask', () => {
  it('reads the class ids from the red channel of the decoded grayscale PNG', () => {
    expect(Array.from(maskValues([0, 0, 0, 255, 1, 1, 1, 255, 2, 2, 2, 255]))).toEqual([0, 1, 2])
  })

  it('counts each class and the foreground share', () => {
    const summary = summarize(mask([[0, 1, 1, 0], [0, 2, 1, 0]]))
    expect(summary.kind).toBe('classes')
    expect(summary.foreground).toBe(0.5)
    expect(summary.classes).toEqual([{ value: 1, pixels: 3, ratio: 0.375 }, { value: 2, pixels: 1, ratio: 0.125 }])
    expect(summarize(mask([[0, 0], [0, 0]]))).toEqual({ kind: 'classes', classes: [], foreground: 0 })
  })

  it('takes a mask of many values for a probability map and counts p ≥ 0.5 as foreground', () => {
    const values = Uint8Array.from({ length: 256 }, (_, value) => value)
    expect(summarize(values)).toEqual({ kind: 'probability', classes: [], foreground: 0.5 })
    expect(summarize(Uint8Array.from({ length: MAX_CLASSES }, (_, value) => value)).kind).toBe('classes')
  })

  it('colours class 1 with the chosen colour and the others from the palette', () => {
    const green = [1, 2, 3] as const
    expect(classColor(1, green)).toBe(green)
    expect(classColor(2, green)).toBe(PALETTE[1])
    expect(classColor(PALETTE.length + 1, green)).toBe(PALETTE[0])
    expect(hexToRgb('#22C55E')).toEqual([34, 197, 94])
    expect(hexToRgb('green')).toBeNull()
  })

  it('paints the classes over a transparent or an opaque background', () => {
    const values = mask([[0, 1, 2]])
    const over = paintMask(values, 'classes', [10, 20, 30])
    expect(Array.from(over)).toEqual([0, 0, 0, 0, 10, 20, 30, 255, ...PALETTE[1], 255])
    const alone = paintMask(values, 'classes', [10, 20, 30], [5, 5, 5, 255])
    expect(Array.from(alone.slice(0, 8))).toEqual([5, 5, 5, 255, 10, 20, 30, 255])
  })

  it('paints a probability map as the colour with the probability as alpha, or blended over the background', () => {
    const values = Uint8Array.from([0, 128, 255])
    expect(alphas(paintMask(values, 'probability', [200, 100, 0]), 3)).toEqual([[0, 128, 255]])
    const blended = paintMask(values, 'probability', [200, 100, 0], [0, 0, 0, 255])
    expect(Array.from(blended.slice(4, 8))).toEqual([100, 50, 0, 255])
  })

  it('outlines a region: its pixels next to another class, not the inside', () => {
    const values = mask([
      [0, 0, 0, 0, 0],
      [0, 1, 1, 1, 0],
      [0, 1, 1, 1, 0],
      [0, 1, 1, 1, 0],
      [0, 0, 0, 0, 0],
    ])
    expect(alphas(paintOutline(values, 5, 5, 'classes', [9, 9, 9]), 5)).toEqual([
      [0, 0, 0, 0, 0],
      [0, 255, 255, 255, 0],
      [0, 255, 0, 255, 0],
      [0, 255, 255, 255, 0],
      [0, 0, 0, 0, 0],
    ])
    // radius 1 draws each boundary pixel as a 3 × 3 square: here the whole image
    expect(alphas(paintOutline(values, 5, 5, 'classes', [9, 9, 9], 1), 5).flat().every(alpha => alpha === 255)).toBe(true)
  })

  it('counts the edge of the image as background, and two touching classes as two outlines', () => {
    const values = mask([[1, 1, 2, 2], [1, 1, 2, 2]])
    const outline = paintOutline(values, 4, 2, 'classes', [9, 9, 9])
    expect(alphas(outline, 4)).toEqual([[255, 255, 255, 255], [255, 255, 255, 255]])
    expect(Array.from(outline.slice(0, 3))).toEqual([9, 9, 9])
    expect(Array.from(outline.slice(8, 11))).toEqual([...PALETTE[1]])
  })

  it('cuts the foreground out of the image', () => {
    const image = [10, 20, 30, 255, 40, 50, 60, 255]
    expect(Array.from(paintCutout(mask([[0, 1]]), 'classes', image))).toEqual([0, 0, 0, 0, 40, 50, 60, 255])
    expect(alphas(paintCutout(Uint8Array.from([0, 102]), 'probability', image), 2)).toEqual([[0, 102]])
  })

  it('thickens the outline with the size of the image, up to a limit', () => {
    expect(outlineRadius(160, 90)).toBe(0)
    expect(outlineRadius(1280, 720)).toBe(2)
    expect(outlineRadius(20000, 100)).toBe(8)
  })
})
