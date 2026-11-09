import { ApiError } from '../../shared/api'
import type { EnumFieldName } from './policy'

export type EnumFailureKind =
  | 'not_editable'
  | 'value_exists'
  | 'validation'
  | 'file_invalid'
  | 'write_failed'
  | 'uncertain'
  | 'unknown'

export interface EnumFailureView {
  kind: EnumFailureKind
  code: string | null
  status: number | null
  detail: string
  traceId: string | null
  fieldErrors: Partial<Record<EnumFieldName, string>>
  /** True only for an explicit file failure. Detail payloads never set this by themselves. */
  blockWrites: boolean
}

function fieldFromLoc(loc: unknown): EnumFieldName | null {
  if (!Array.isArray(loc)) return null
  for (let index = loc.length - 1; index >= 0; index -= 1) {
    const part = loc[index]
    if (part === 'value' || part === 'label' || part === 'description') return part
  }
  return null
}

function validationFields(
  errors: readonly Record<string, unknown>[] | null,
  fallback: string,
): Partial<Record<EnumFieldName, string>> {
  const fieldErrors: Partial<Record<EnumFieldName, string>> = {}
  for (const entry of errors ?? []) {
    const field = fieldFromLoc(entry.loc)
    if (!field) continue
    const message = typeof entry.msg === 'string' && entry.msg.trim() ? entry.msg.trim() : fallback
    fieldErrors[field] = fieldErrors[field] ? `${fieldErrors[field]}；${message}` : message
  }
  return fieldErrors
}

function view(
  kind: EnumFailureKind,
  input: { code?: string | null; status?: number | null; detail: string; traceId?: string | null; fieldErrors?: EnumFailureView['fieldErrors']; blockWrites?: boolean },
): EnumFailureView {
  return {
    kind,
    code: input.code ?? null,
    status: input.status ?? null,
    detail: input.detail,
    traceId: input.traceId ?? null,
    fieldErrors: input.fieldErrors ?? {},
    blockWrites: input.blockWrites ?? false,
  }
}

/** Map a thrown API/network error into the view model the page renders. */
export function toEnumFailure(error: unknown): EnumFailureView {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return view('uncertain', { detail: '请求超时，无法确认枚举服务是否已经处理这次操作。', status: null })
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return view('uncertain', { detail: '请求已中断，无法确认枚举服务是否已经处理这次操作。', status: null })
  }
  if (error instanceof ApiError) {
    const detail = error.detail ?? error.message
    const status = error.status
    const base = { code: error.code, status, detail, traceId: error.traceId }
    const noClearBody = status === null || status === 504 || (error.code === null && status >= 500)
    if (noClearBody) return view('uncertain', base)
    switch (error.code) {
      case 'ENUM_NOT_EDITABLE':
        return view('not_editable', base)
      case 'ENUM_VALUE_EXISTS':
        return view('value_exists', { ...base, fieldErrors: { value: '该取值已存在。' } })
      case 'VALIDATION_ERROR':
        return view('validation', { ...base, fieldErrors: validationFields(error.errors, detail) })
      case 'ENUM_FILE_INVALID':
        return view('file_invalid', { ...base, blockWrites: true })
      case 'ENUM_FILE_WRITE_FAILED':
        return view('write_failed', base)
      default:
        return view('unknown', base)
    }
  }
  const detail = error instanceof Error && error.message ? error.message : '枚举请求失败，请稍后重试。'
  return view('unknown', { detail })
}
