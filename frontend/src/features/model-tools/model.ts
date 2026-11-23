import type {
  GdinoDetectionWire, GdinoResponseWire, RecipeCandidateWire, RecipeRegionWire, RecipeToolResponseWire, SegmentResponseWire,
} from '../../shared/api'
import { asFiniteNumber, asInteger, asText, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'

/**
 * The tools of the page: GroundingDINO detection, segmentation, and 业务搜图's 菜谱识别 laid open
 * (POST /api/tools/gdino, /api/tools/segment, /api/tools/recipe).
 */
export type ToolKey = 'gdino' | 'segment' | 'recipe'

export function isToolKey(value: unknown): value is ToolKey {
  return value === 'gdino' || value === 'segment' || value === 'recipe'
}

/** AUTO_SEARCH_MAX_REGIONS: the most boxes 业务搜图 uses. */
export const MAX_REGIONS_LIMIT = 20

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

/** A dish of the recipe model's gallery close to an image. */
export interface RecipeCandidate {
  label: string
  /** Cosine similarity, -1–1. */
  score: number
}

/** A box 业务搜图's 菜谱识别 classifies: its crop's closest dishes. */
export interface RecipeRegion {
  /** GroundingDINO's word for the box. */
  label: string
  /** Detection confidence. */
  score: number
  /** x1, y1, x2, y2 in pixels of the image the server decoded. */
  box: [number, number, number, number]
  /** Best first; the first is the one 业务搜图 goes by. */
  candidates: RecipeCandidate[]
}

export interface RecipeResult {
  width: number
  height: number
  /** What was used: the request's, or AUTO_SEARCH_REGION_CONFIG's where the request left them out. */
  caption: string
  boxThreshold: number
  textThreshold: number
  maxRegions: number
  /** RECIPE_DETECT_CONFIG.threshold: what 业务搜图 goes by. */
  threshold: number
  /** Boxes GroundingDINO gave, before the small ones were dropped and maxRegions applied. */
  detected: number
  /** Highest detection score first. */
  regions: RecipeRegion[]
  /** What 业务搜图 returns at `threshold`, as the server worked it out. */
  names: string[]
  /** The whole image's closest dishes: for comparison, 业务搜图 classifies the boxes alone. */
  wholeImage: RecipeCandidate[]
  latencyMs: number
  classifyMs: number
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

/** Cosine scores can overshoot ±1 slightly in FP32. */
const COSINE_EPSILON = 1e-6

const cosine = (value: unknown): number | null => {
  const number = asFiniteNumber(value)
  return number !== null && Math.abs(number) <= 1 + COSINE_EPSILON ? Math.min(1, Math.max(-1, number)) : null
}

function toCandidates(value: unknown): RecipeCandidate[] | null {
  if (!Array.isArray(value)) return null
  const entries = untrustedList<RecipeCandidateWire>(value)
  const candidates = entries.map(raw => {
    const score = cosine(raw.score)
    return typeof raw.label === 'string' && score !== null ? { label: raw.label.trim(), score } : null
  })
  return entries.length === value.length && candidates.every(entry => entry !== null) ? candidates as RecipeCandidate[] : null
}

function toRecipeRegion(raw: Untrusted<RecipeRegionWire>, width: number, height: number): RecipeRegion | null {
  const detection = toDetection({ label: raw.label, score: raw.score, box: raw.box }, width, height)
  const candidates = toCandidates(raw.candidates)
  return detection && candidates ? { ...detection, candidates } : null
}

/** POST /api/tools/recipe body, or null when it is not one (a box outside the image included). */
export function toRecipeResult(body: unknown): RecipeResult | null {
  const raw = untrusted<RecipeToolResponseWire>(body)
  const width = size(raw?.width)
  const height = size(raw?.height)
  const boxThreshold = unit(raw?.box_threshold)
  const textThreshold = unit(raw?.text_threshold)
  const threshold = unit(raw?.threshold)
  const maxRegions = size(raw?.max_regions)
  const detected = asInteger(raw?.detected)
  const latencyMs = asFiniteNumber(raw?.latency_ms)
  const classifyMs = asFiniteNumber(raw?.classify_ms)
  const wholeImage = toCandidates(raw?.whole_image)
  if (raw?.ok !== true || width === null || height === null || boxThreshold === null || textThreshold === null
    || threshold === null || maxRegions === null || detected === null || detected < 0 || latencyMs === null
    || classifyMs === null || typeof raw.caption !== 'string' || !Array.isArray(raw.regions) || !Array.isArray(raw.names)
    || !wholeImage) return null
  const entries = untrustedList<RecipeRegionWire>(raw.regions)
  const regions = entries.map(entry => toRecipeRegion(entry, width, height))
  if (entries.length !== raw.regions.length || regions.some(entry => entry === null)) return null
  return {
    width, height, caption: raw.caption, boxThreshold, textThreshold, maxRegions, threshold, detected,
    regions: regions as RecipeRegion[],
    names: raw.names.flatMap(name => (typeof name === 'string' && name.trim() ? [name.trim()] : [])),
    wholeImage, latencyMs, classifyMs,
  }
}

/** Whether 业务搜图 takes a box's closest dish: it has a name and its score is above the threshold (not equal). */
export function isRecognised(candidate: RecipeCandidate | undefined, threshold: number): boolean {
  return candidate !== undefined && candidate.label !== '' && candidate.score > threshold
}

/**
 * The dish names 业务搜图 returns at `threshold` (the server's dish_names): each box's closest dish
 * when recognised, each name once at its best score, best first, boxes in order on a tie.
 */
export function dishNames(regions: readonly RecipeRegion[], threshold: number): string[] {
  const best = new Map<string, number>()
  for (const { candidates: [closest] } of regions) {
    if (closest && isRecognised(closest, threshold) && closest.score > (best.get(closest.label) ?? -Infinity)) best.set(closest.label, closest.score)
  }
  return [...best.keys()].sort((a, b) => best.get(b)! - best.get(a)!)
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

/** A threshold as the page shows it: three decimals at most, no trailing zeros (0.95 → "0.95", 0.9256 → "0.926"). */
export function formatThreshold(value: number): string {
  return String(Number(value.toFixed(3)))
}

/** max_regions typed by hand: a whole number in 1–20; null when blank (the configured value); undefined when it is not one. */
export function maxRegionsFrom(text: string): number | null | undefined {
  const trimmed = text.trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  return Number.isInteger(value) && value >= 1 && value <= MAX_REGIONS_LIMIT ? value : undefined
}
