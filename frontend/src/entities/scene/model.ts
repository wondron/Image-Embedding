/** Scene entity — single source for codes, legacy mapping, heating flags (M6 C1–C3 / R22). */

export const DEFAULT_SCENE = 'general' as const

export type SceneCode = string

export interface SceneSeed {
  code: SceneCode
  /** Chinese product label */
  label: string
  /** Short hint for select option */
  hint: string
  includeAll: boolean
  outputsHeating: boolean
}

/** @deprecated Historical fixtures only. Runtime authority is GET /api/scenes. */
export const SCENE_SEEDS: readonly SceneSeed[] = [
  { code: 'general', label: '通用', hint: '日常 · include_all', includeAll: true, outputsHeating: false },
  { code: 'oven', label: '一体机', hint: '加热输出 ON', includeAll: false, outputsHeating: true },
  { code: 'range_hood', label: '抽油烟机', hint: '', includeAll: false, outputsHeating: false },
  { code: 'us_diet', label: '美国饮食', hint: '', includeAll: false, outputsHeating: false },
  { code: 'cn_diet', label: '中国饮食', hint: '', includeAll: false, outputsHeating: false },
  { code: 'jp_diet', label: '日本饮食', hint: '', includeAll: false, outputsHeating: false },
] as const

const SEED_BY_CODE = Object.fromEntries(SCENE_SEEDS.map(s => [s.code, s])) as Record<SceneCode, SceneSeed>

/** Read-only legacy → v0.1 mapping. Never write old codes to PG API. */
const LEGACY_SCENE_MAP: Record<string, SceneCode> = {
  normal: 'general',
  device: 'oven',
}

export function isSceneCode(value: string): value is SceneCode {
  return /^[a-z][a-z0-9_]*$/.test(value) && value.length <= 32
}

/**
 * Canonicalize legacy tokens once; preserve case and arbitrary current codes.
 * Old codes map once; unknown codes pass through trimmed (backend may error — do not treat as empty library).
 */
export function normalizeSceneCode(value: unknown, fallback: SceneCode = DEFAULT_SCENE): string {
  if (typeof value !== 'string') return fallback
  const trimmed = value.trim()
  if (!trimmed) return fallback
  if (Object.prototype.hasOwnProperty.call(LEGACY_SCENE_MAP, trimmed)) return LEGACY_SCENE_MAP[trimmed]
  return trimmed
}

/** Value safe to send on API query/body — always mapped off legacy. */
export function toApiSceneCode(value: unknown, fallback: SceneCode = DEFAULT_SCENE): string {
  const code = normalizeSceneCode(value, fallback)
  // Never emit legacy codes
  if (code === 'normal') return 'general'
  if (code === 'device') return 'oven'
  return code
}

export function getSceneSeed(code: string): SceneSeed | undefined {
  const normalized = normalizeSceneCode(code, DEFAULT_SCENE)
  return Object.prototype.hasOwnProperty.call(SEED_BY_CODE, normalized) ? SEED_BY_CODE[normalized] : undefined
}

export function sceneDisplayLabel(code: string): string {
  return code
}

export function sceneOutputsHeating(code: string, apiFlag?: boolean | null): boolean {
  if (apiFlag === true) return true
  if (apiFlag === false) return false
  void code; return false
}
