export const SCORE_DEFAULT = 0.55
export const SCORE_MIN = 0.30
export const SCORE_MAX = 1
export const SCORE_STEP = 0.01

/** Clamp to legal set {0} ∪ [0.30, 1] (step 0.01). Sentinel 0 only when reject-off. */
export function clampScoreThreshold(value: number, allowZero = false): number {
  if (!Number.isFinite(value)) return SCORE_DEFAULT
  if (allowZero && value === 0) return 0
  const stepped = Math.round(value / SCORE_STEP) * SCORE_STEP
  return Math.min(SCORE_MAX, Math.max(SCORE_MIN, Number(stepped.toFixed(2))))
}

export function formatScore(value: number): string {
  return value.toFixed(2)
}