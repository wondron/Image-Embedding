import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'

type ObjectValue = Record<string, unknown>

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : {}
}

/** Prefer scene_label when present; else generic category. */
export function displayCategoryLabel(item: {
  sceneLabel?: string | null
  category?: string | null
}): string | null {
  return text(item.sceneLabel) ?? text(item.category) ?? null
}

export function extractSceneLabel(raw: ObjectValue): string | null {
  return text(raw.scene_label) ?? text(raw.sceneLabel) ?? text(object(raw.details).scene_label)
}

export function extractHeatingGroup(raw: ObjectValue): string | null {
  return (
    text(raw.heating_group)
    ?? text(raw.heating_group_name)
    ?? text(raw.heatingGroup)
    ?? text(object(raw.details).heating_group)
    ?? null
  )
}

export function extractFoodState(raw: ObjectValue): string | null {
  return text(raw.food_state) ?? text(raw.foodState) ?? text(object(raw.details).food_state)
}

export function extractOutputsHeating(raw: ObjectValue, responseFlag?: boolean): boolean | undefined {
  if (typeof raw.outputs_heating === 'boolean') return raw.outputs_heating
  if (typeof responseFlag === 'boolean') return responseFlag
  return undefined
}

/** Prefer thumb_url / original_url for self-built results; keep legacy image_url fallback. */
export function pickSearchImageUrls(raw: ObjectValue): { imageUrl: string | null; thumbnailUrl: string | null } {
  const thumb =
    safeHttpUrl(raw.thumb_url)
    ?? safeHttpUrl(raw.thumbnail_url)
    ?? null
  const original =
    safeHttpUrl(raw.original_url)
    ?? safeHttpUrl(raw.image_url)
    ?? null
  return {
    imageUrl: original ?? thumb,
    thumbnailUrl: thumb ?? original,
  }
}