/**
 * The segmentation mask, drawn in the browser: the page asks the segment tool for the mask only
 * (output_vis=false) and turns it into each view here. Pure pixel arithmetic on typed arrays, so it
 * runs (and is tested) without a canvas: the view decodes the PNGs to RGBA, these functions return
 * the RGBA it puts on its canvases.
 */

/** One byte per pixel: the class id (0 = background), or a 0–255 probability. */
export type MaskValues = Uint8Array
export type Rgb = readonly [number, number, number]
export type Rgba = readonly [number, number, number, number]
type Pixels = Uint8ClampedArray<ArrayBuffer>

/** More distinct values than this: a probability map (a single-channel model), not class ids. */
export const MAX_CLASSES = 32

export interface MaskClass {
  value: number
  pixels: number
  /** Share of the image, 0–1. */
  ratio: number
}

export interface MaskSummary {
  kind: 'classes' | 'probability'
  /** classes: each class id present except background (0), most pixels first; probability: none. */
  classes: MaskClass[]
  /** Share of the image that is foreground (0–1): class ≠ 0, or probability ≥ 0.5. */
  foreground: number
}

/** Colours for class ids 1, 2, … (class 1 and a probability map can take another, see classColor). */
export const PALETTE: readonly Rgb[] = [
  [34, 197, 94], [249, 115, 22], [59, 130, 246], [236, 72, 153],
  [234, 179, 8], [20, 184, 166], [168, 85, 247], [239, 68, 68],
]

/** The values of a grayscale mask decoded to RGBA (R = G = B = the value). */
export function maskValues(rgba: ArrayLike<number>): MaskValues {
  const values = new Uint8Array(Math.floor(rgba.length / 4))
  for (let i = 0; i < values.length; i++) values[i] = rgba[i * 4]
  return values
}

export function summarize(values: MaskValues): MaskSummary {
  const counts = new Array<number>(256).fill(0)
  for (let i = 0; i < values.length; i++) counts[values[i]]++
  const total = values.length
  const share = (pixels: number) => (total ? pixels / total : 0)
  const present = counts.flatMap((count, value) => (count > 0 ? [value] : []))
  if (present.length > MAX_CLASSES) {
    const above = counts.slice(128).reduce((sum, count) => sum + count, 0)
    return { kind: 'probability', classes: [], foreground: share(above) }
  }
  const classes = present
    .filter(value => value !== 0)
    .map(value => ({ value, pixels: counts[value], ratio: share(counts[value]) }))
    .sort((a, b) => b.pixels - a.pixels || a.value - b.value)
  return { kind: 'classes', classes, foreground: share(total - counts[0]) }
}

/** Class 1 (and a probability map) in `first`, the other classes in the palette's order. */
export function classColor(value: number, first: Rgb): Rgb {
  return value <= 1 ? first : PALETTE[(value - 1) % PALETTE.length]
}

/** '#22c55e' → [34, 197, 94]; null when it is not a 6-digit hex colour. */
export function hexToRgb(hex: string): Rgb | null {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  return match ? [parseInt(match[1], 16), parseInt(match[2], 16), parseInt(match[3], 16)] : null
}

/**
 * The mask in colour: each class pixel in its class colour, background `background` (transparent:
 * for 叠加 over the image; opaque: the mask on its own, for 并排). A probability map is the colour
 * blended over the background by probability.
 */
export function paintMask(values: MaskValues, kind: MaskSummary['kind'], first: Rgb, background: Rgba = [0, 0, 0, 0]): Pixels {
  const out = new Uint8ClampedArray(values.length * 4)
  const [br, bg, bb, ba] = background
  for (let i = 0; i < values.length; i++) {
    const value = values[i]
    const o = i * 4
    if (kind === 'probability') {
      const a = value / 255
      if (ba === 0) {
        out[o] = first[0]; out[o + 1] = first[1]; out[o + 2] = first[2]; out[o + 3] = value
      } else {
        out[o] = br + (first[0] - br) * a; out[o + 1] = bg + (first[1] - bg) * a; out[o + 2] = bb + (first[2] - bb) * a; out[o + 3] = ba
      }
    } else if (value === 0) {
      out[o] = br; out[o + 1] = bg; out[o + 2] = bb; out[o + 3] = ba
    } else {
      const [r, g, b] = classColor(value, first)
      out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = 255
    }
  }
  return out
}

/** How thick 轮廓 draws a boundary: about 1/640 of the longer side on either side of it (0 = one pixel). */
export function outlineRadius(width: number, height: number): number {
  return Math.min(8, Math.round(Math.max(width, height) / 640))
}

/**
 * 轮廓: the boundary of each region, in its class colour, transparent elsewhere. A boundary pixel is a
 * foreground pixel with a 4-neighbour of another class (outside the image counts as background);
 * each is drawn as a square of side 2·radius+1. A probability map is cut at 0.5 first.
 */
export function paintOutline(values: MaskValues, width: number, height: number, kind: MaskSummary['kind'], first: Rgb, radius = 0): Pixels {
  const out = new Uint8ClampedArray(width * height * 4)
  const classOf = kind === 'probability'
    ? (i: number) => (values[i] >= 128 ? 1 : 0)
    : (i: number) => values[i]
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x
      const value = classOf(i)
      if (value === 0) continue
      const edge = x === 0 || y === 0 || x === width - 1 || y === height - 1
        || classOf(i - 1) !== value || classOf(i + 1) !== value || classOf(i - width) !== value || classOf(i + width) !== value
      if (!edge) continue
      const [r, g, b] = classColor(value, first)
      for (let yy = Math.max(0, y - radius); yy <= Math.min(height - 1, y + radius); yy++) {
        for (let xx = Math.max(0, x - radius); xx <= Math.min(width - 1, x + radius); xx++) {
          const o = (yy * width + xx) * 4
          out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = 255
        }
      }
    }
  }
  return out
}

/** 抠图: the image's own pixels where the mask is foreground (a probability map: alpha = probability), transparent elsewhere. */
export function paintCutout(values: MaskValues, kind: MaskSummary['kind'], image: ArrayLike<number>): Pixels {
  const out = new Uint8ClampedArray(values.length * 4)
  for (let i = 0; i < values.length; i++) {
    const alpha = kind === 'probability' ? values[i] : values[i] !== 0 ? 255 : 0
    if (!alpha) continue
    const o = i * 4
    out[o] = image[o]; out[o + 1] = image[o + 1]; out[o + 2] = image[o + 2]
    out[o + 3] = Math.round((image[o + 3] * alpha) / 255)
  }
  return out
}
