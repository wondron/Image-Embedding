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
  /** Display thumb from wire `thumb_url` only (https). */
  imageUrl: string | null
  /** Wire `original_url` only. */
  originalUrl: string | null
  /** Always null — wire has no title (P5). UI may show category / id. */
  title: string | null
  category: string | null
  /** Always [] — tags have no PG column. */
  tags: string[]
  scenes: string[]
  /** Rich membership when API provides origin; else derived from scenes[]. */
  sceneMemberships: SceneMembership[]
  foodState: FoodState
  heatingGroup: string | null
  createdAt: string | null
  width: number | null
  height: number | null
  raw: Record<string, unknown>
}

/**
 * Display URL: wire `thumb_url` only (List map §6.1.4).
 * FORBIDDEN: image_url / thumbnail_url / path / url aliases (P2).
 */
export function pickImageDisplayUrl(raw: ObjectValue): string | null {
  return safeHttpUrl(raw.thumb_url)
}

/** Wire `original_url` only. */
export function pickOriginalUrl(raw: ObjectValue): string | null {
  return safeHttpUrl(raw.original_url)
}

function parseFoodState(value: unknown): FoodState {
  const v = text(value)?.toLowerCase()
  if (v === 'raw' || v === 'cooked' || v === 'frozen') return v
  return null
}

function parseMemberships(raw: ObjectValue): SceneMembership[] {
  const detailed = raw.scene_memberships
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
  // id only — never qdrant_point_id (P11)
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
    title: null,
    category: text(raw.category),
    tags: [],
    scenes,
    sceneMemberships: memberships,
    foodState: parseFoodState(raw.food_state),
    heatingGroup: text(raw.heating_group),
    createdAt: text(raw.created_at),
    width: positiveNumber(raw.width),
    height: positiveNumber(raw.height),
    raw,
  }
}

export { DEFAULT_SCENE }
