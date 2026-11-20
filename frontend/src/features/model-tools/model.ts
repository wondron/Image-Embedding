import type { GdinoDetectionWire, GdinoResponseWire, SegmentResponseWire } from '../../shared/api'
import { asFiniteNumber, asInteger, asText, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'

/** The two tools of the page: GroundingDINO detection and segmentation (POST /api/tools/gdino, /api/tools/segment). */
export type ToolKey = 'gdino' | 'segment'

export function isToolKey(value: unknown): value is ToolKey {
  return value === 'gdino' || value === 'segment'
}

export interface Detection {
  /** The caption's word(s) matched to the box; '' when none passed text_threshold. */
  label: string
  score: number
  /** x1, y1, x2, y2 in pixels of the image the server decoded. */
  box: [number, number, number, number]
}

export interface DetectionResult {
  width: number
  height: number
  /** What was used: the request's, or GROUNDINGDINO_CONFIG's where the request left them out. */
  caption: string
  boxThreshold: number
  textThreshold: number
  latencyMs: number
  /** Highest score first. */
  detections: Detection[]
}

export interface SegmentResult {
  width: number
  height: number
  latencyMs: number
  /** The grayscale mask as a data: URL (class id or 0–255 probability per pixel). */
  maskUrl: string
}

const size = (value: unknown): number | null => {
  const number = asInteger(value)
  return number !== null && number > 0 ? number : null
}

const unit = (value: unknown): number | null => {
  const number = asFiniteNumber(value)
  return number !== null && number >= 0 && number <= 1 ? number : null
}

function toDetection(raw: Untrusted<GdinoDetectionWire>, width: number, height: number): Detection | null {
  const score = unit(raw.score)
  const box = Array.isArray(raw.box) ? raw.box.map(asFiniteNumber) : []
  if (score === null || box.length !== 4 || box.some(value => value === null)) return null
  const [x1, y1, x2, y2] = box as number[]
  if (x1 < 0 || y1 < 0 || x2 > width || y2 > height || x2 <= x1 || y2 <= y1) return null
  return { label: typeof raw.label === 'string' ? raw.label.trim() : '', score, box: [x1, y1, x2, y2] }
}

/** POST /api/tools/gdino body, or null when it is not one (a box outside the image included). */
export function toDetectionResult(body: unknown): DetectionResult | null {
  const raw = untrusted<GdinoResponseWire>(body)
  const width = size(raw?.width)
  const height = size(raw?.height)
  const boxThreshold = unit(raw?.box_threshold)
  const textThreshold = unit(raw?.text_threshold)
  const latencyMs = asFiniteNumber(raw?.latency_ms)
  if (raw?.ok !== true || width === null || height === null || boxThreshold === null || textThreshold === null
    || latencyMs === null || typeof raw.caption !== 'string' || !Array.isArray(raw.detections)) return null
  const entries = untrustedList<GdinoDetectionWire>(raw.detections)
  const detections = entries.map(entry => toDetection(entry, width, height))
  if (entries.length !== raw.detections.length || detections.some(entry => entry === null)) return null
  return {
    width, height, caption: raw.caption, boxThreshold, textThreshold, latencyMs,
    detections: (detections as Detection[]).sort((a, b) => b.score - a.score),
  }
}

/** POST /api/tools/segment body, or null when it is not one. */
export function toSegmentResult(body: unknown): SegmentResult | null {
  const raw = untrusted<SegmentResponseWire>(body)
  const width = size(raw?.width)
  const height = size(raw?.height)
  const latencyMs = asFiniteNumber(raw?.latency_ms)
  const mask = asText(raw?.mask_png_base64)
  if (raw?.ok !== true || width === null || height === null || latencyMs === null || !mask || !/^[A-Za-z0-9+/]+=*$/.test(mask)) return null
  return { width, height, latencyMs, maskUrl: `data:image/png;base64,${mask}` }
}

/**
 * What 「检测什么」 sends as text_range: the words typed, separated by 「.」, commas, 「、」, semicolons or
 * new lines, joined the way GroundingDINO separates categories (「food . plate」). null when nothing is
 * typed: the server uses GROUNDINGDINO_CONFIG.caption.
 */
export function captionFrom(text: string): string | null {
  const terms = text.split(/[.,，、;；\n]+/).map(term => term.trim()).filter(Boolean)
  return terms.length ? terms.join(' . ') : null
}

/** A threshold typed by hand: a number in 0–1; null when blank (the configured value); undefined when it is not one. */
export function thresholdFrom(text: string): number | null | undefined {
  const trimmed = text.trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : undefined
}
