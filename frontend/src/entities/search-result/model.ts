import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'

type ObjectValue = Record<string, unknown>

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** Prefer scene_label when present; else generic category. */
export function displayCategoryLabel(item: {
  sceneLabel?: string | null
  category?: string | null
}): string | null {
  return text(item.sceneLabel) ?? text(item.category) ?? null
}

/** Wire `scene_label` only (string | null). No camel / details dual-source. */
export function extractSceneLabel(raw: ObjectValue): string | null {
  return text(raw.scene_label)
}

/** Wire `heating_group` only. */
export function extractHeatingGroup(raw: ObjectValue): string | null {
  return text(raw.heating_group)
}

/** Wire `food_state` only. */
export function extractFoodState(raw: ObjectValue): string | null {
  return text(raw.food_state)
}

/**
 * Envelope-only `outputs_heating` (P9).
 * Hit-level `outputs_heating` is ignored — trust the response flag only.
 */
export function extractOutputsHeating(_raw: ObjectValue, responseFlag?: boolean): boolean | undefined {
  return typeof responseFlag === 'boolean' ? responseFlag : undefined
}

/**
 * §6.1 URL map: thumb_url → thumbnailUrl, original_url → imageUrl.
 * FORBIDDEN: image_url / thumbnail_url / details.* fallbacks (P2).
 */
export function pickSearchImageUrls(raw: ObjectValue): { imageUrl: string | null; thumbnailUrl: string | null } {
  const thumbnailUrl = safeHttpUrl(raw.thumb_url)
  const imageUrl = safeHttpUrl(raw.original_url)
  return { imageUrl, thumbnailUrl }
}
