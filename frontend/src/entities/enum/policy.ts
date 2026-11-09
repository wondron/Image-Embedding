/**
 * Client checks that match app.core.enums. They do not decide editability;
 * editable still comes from the server payload.
 */
export const ENUM_VALUE_MAX_LEN = 32
export const ENUM_LABEL_MAX_LEN = 64
export const ENUM_DESCRIPTION_MAX_LEN = 256
export const ENUM_MAX_VALUES = 100
export const ENUM_VALUE_PATTERN = /^[a-z][a-z0-9_]*$/

export type EnumFieldName = 'value' | 'label' | 'description'

export interface AppendDraft {
  value: string
  label: string
  description: string
}

export interface AppendPayload {
  value: string
  label: string
  description: string
}

export function charLength(value: string): number {
  return Array.from(value).length
}

export function validateAppendDraft(draft: AppendDraft): {
  ok: true
  body: AppendPayload
} | {
  ok: false
  fieldErrors: Partial<Record<EnumFieldName, string>>
} {
  const value = draft.value.trim()
  const label = draft.label.trim()
  const description = typeof draft.description === 'string' ? draft.description : ''
  const fieldErrors: Partial<Record<EnumFieldName, string>> = {}

  if (!value) fieldErrors.value = '请填写取值。'
  else if (charLength(value) > ENUM_VALUE_MAX_LEN) fieldErrors.value = '取值长度需为 1–32 个字符。'
  else if (!ENUM_VALUE_PATTERN.test(value)) fieldErrors.value = '取值需以小写字母开头，并且只包含小写字母、数字或下划线。'

  if (!label) fieldErrors.label = '请填写名称。'
  else if (charLength(label) > ENUM_LABEL_MAX_LEN) fieldErrors.label = '名称长度需为 1–64 个字符。'

  if (charLength(description) > ENUM_DESCRIPTION_MAX_LEN) fieldErrors.description = '说明最长 256 个字符。'

  if (fieldErrors.value || fieldErrors.label || fieldErrors.description) return { ok: false, fieldErrors }
  return { ok: true, body: { value, label, description } }
}

export interface PrePostItem {
  editable: boolean
  values: readonly unknown[]
}

export type PrePostDecision =
  | { action: 'post' }
  | { action: 'stop'; reason: 'stale' | 'file_error' | 'missing' | 'not_editable' | 'full' }

/** Fresh list health check immediately before POST. A stale result must not post. */
export function decidePrePost(input: {
  fresh: boolean
  fileError: string | null
  item: PrePostItem | null
}): PrePostDecision {
  if (!input.fresh) return { action: 'stop', reason: 'stale' }
  if (input.fileError) return { action: 'stop', reason: 'file_error' }
  if (!input.item) return { action: 'stop', reason: 'missing' }
  if (input.item.editable !== true) return { action: 'stop', reason: 'not_editable' }
  if (input.item.values.length >= ENUM_MAX_VALUES) return { action: 'stop', reason: 'full' }
  return { action: 'post' }
}

export interface ProbeItem {
  editable: boolean
  values: readonly { value: string }[]
}

export type UncertainProbeDecision =
  | { action: 'exists' }
  | { action: 'allow_retry' }
  | { action: 'keep_blocked'; reason: 'probe_failed' | 'file_error' | 'missing' | 'not_editable' }

/**
 * After a POST with no clear error body. Manual retry is allowed only when a
 * fresh healthy read shows that this value is still absent.
 */
export function resolveUncertainProbe(input: {
  probeOk: boolean
  fileError: string | null
  item: ProbeItem | null
  value: string
}): UncertainProbeDecision {
  if (!input.probeOk) return { action: 'keep_blocked', reason: 'probe_failed' }
  const present = input.item?.values.some(row => row.value === input.value) ?? false
  if (present) return { action: 'exists' }
  if (input.fileError) return { action: 'keep_blocked', reason: 'file_error' }
  if (!input.item) return { action: 'keep_blocked', reason: 'missing' }
  if (input.item.editable !== true) return { action: 'keep_blocked', reason: 'not_editable' }
  return { action: 'allow_retry' }
}
