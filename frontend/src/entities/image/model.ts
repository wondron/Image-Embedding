import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import { DEFAULT_SCENE, normalizeSceneCode } from '../scene'

type ObjectValue = Record<string, unknown>

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : {}
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function positiveNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(text).filter((entry): entry is string => entry !== null)
}

export type FoodState = 'raw' | 'cooked' | 'frozen' | null

export interface SceneMembership {
  code: string
  /** rule | manual — when API absent, omit */
  origin?: 'rule' | 'manual' | string
}

export interface LibraryImageItem {
  id: string
  /** Display thumb: https only — never filesystem path. */
  imageUrl: string | null
  /** Prefer original for detail / open. */
  originalUrl: string | null
  title: string | null
  category: string | null
  tags: string[]
  scenes: string[]
  /** Rich membership when API provides origin; else derived from scenes[]. */
  sceneMemberships: SceneMembership[]
  foodState: FoodState
  createdAt: string | null
  width: number | null
  height: number | null
  raw: Record<string, unknown>
}

/**
 * Pick https display URL: thumb_url → original_url → legacy image_url.
 * FORBIDDEN: path / local filesystem as src.
 */
export function pickImageDisplayUrl(raw: ObjectValue): string | null {
  return (
    safeHttpUrl(raw.thumb_url)
    ?? safeHttpUrl(raw.original_url)
    ?? safeHttpUrl(raw.image_url)
    ?? safeHttpUrl(raw.url)
    ?? null
  )
}

export function pickOriginalUrl(raw: ObjectValue): string | null {
  return (
    safeHttpUrl(raw.original_url)
    ?? safeHttpUrl(raw.image_url)
    ?? safeHttpUrl(raw.url)
    ?? safeHttpUrl(raw.thumb_url)
    ?? null
  )
}

function parseFoodState(value: unknown): FoodState {
  const v = text(value)?.toLowerCase()
  if (v === 'raw' || v === 'cooked' || v === 'frozen') return v
  return null
}

function parseMemberships(raw: ObjectValue): SceneMembership[] {
  const detailed = raw.scene_memberships ?? raw.image_scenes ?? raw.memberships
  if (Array.isArray(detailed)) {
    return detailed
      .map(entry => {
        const row = object(entry)
        const code = text(row.code) ?? text(row.scene) ?? text(row.scene_code)
        if (!code) return null
        const origin = text(row.origin) ?? undefined
        return { code: normalizeSceneCode(code, code as never) || code, ...(origin ? { origin } : {}) }
      })
      .filter((m): m is SceneMembership => m !== null)
  }
  return stringList(raw.scenes).map(code => ({ code: normalizeSceneCode(code, code as never) || code }))
}

export function normalizeLibraryItem(raw: ObjectValue): LibraryImageItem | null {
  const id = text(raw.id) ?? (typeof raw.id === 'number' && Number.isFinite(raw.id) ? String(raw.id) : null)
  if (!id) return null
  const memberships = parseMemberships(raw)
  const scenes = memberships.length
    ? memberships.map(m => m.code)
    : stringList(raw.scenes).map(c => normalizeSceneCode(c, c as never) || c)
  return {
    id,
    imageUrl: pickImageDisplayUrl(raw),
    originalUrl: pickOriginalUrl(raw),
    title: text(raw.title),
    category: text(raw.category),
    tags: stringList(raw.tags),
    scenes,
    sceneMemberships: memberships,
    foodState: parseFoodState(raw.food_state),
    createdAt: text(raw.created_at),
    width: positiveNumber(raw.width),
    height: positiveNumber(raw.height),
    raw,
  }
}

export { DEFAULT_SCENE }