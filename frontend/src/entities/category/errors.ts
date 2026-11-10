import { ApiError } from '../../shared/api'
import type { CategoryFieldErrors, CategoryFieldName } from './policy'

export type CategoryFailureKind =
  | 'not_found'
  | 'code_exists'
  | 'in_use'
  | 'validation'
  | 'uncertain'
  | 'unknown'

export interface CategoryFailureView {
  kind: CategoryFailureKind
  code: string | null
  status: number | null
  detail: string
  traceId: string | null
  fieldErrors: CategoryFieldErrors
}

/** Request field (wire name) → form field. names[0] is the zh primary name the form sends first. */
function fieldFromLoc(loc: unknown): CategoryFieldName | null {
  if (!Array.isArray(loc)) return null
  const index = loc.findIndex(part => typeof part === 'string' && part !== 'body')
  if (index < 0) return null
  switch (loc[index]) {
    case 'code': return 'code'
    case 'parent_id': return 'parentId'
    case 'names': return loc[index + 1] === 0 ? 'zhName' : 'otherNames'
    case 'cuisines': return 'cuisines'
    case 'heating_group_id': return 'heatingGroupId'
    case 'sort_order': return 'sortOrder'
    default: return null
  }
}

function validationFields(errors: readonly Record<string, unknown>[] | null, fallback: string): CategoryFieldErrors {
  const fieldErrors: CategoryFieldErrors = {}
  for (const entry of errors ?? []) {
    const field = fieldFromLoc(entry.loc)
    if (!field) continue
    const message = typeof entry.msg === 'string' && entry.msg.trim() ? entry.msg.trim() : fallback
    fieldErrors[field] = fieldErrors[field] ? `${fieldErrors[field]}；${message}` : message
  }
  return fieldErrors
}

function view(
  kind: CategoryFailureKind,
  input: { code?: string | null; status?: number | null; detail: string; traceId?: string | null; fieldErrors?: CategoryFieldErrors },
): CategoryFailureView {
  return {
    kind,
    code: input.code ?? null,
    status: input.status ?? null,
    detail: input.detail,
    traceId: input.traceId ?? null,
    fieldErrors: input.fieldErrors ?? {},
  }
}

/**
 * Map a thrown API / network error into what the page renders.
 * 'uncertain' means no clear answer arrived (timeout, dropped connection, a gateway 5xx without
 * an error code): the write may or may not have happened, so the page reloads the tree.
 */
export function toCategoryFailure(error: unknown): CategoryFailureView {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return view('uncertain', { detail: '请求超时，无法确认类别服务是否已经处理这次操作。' })
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return view('uncertain', { detail: '请求已中断，无法确认类别服务是否已经处理这次操作。' })
  }
  if (error instanceof ApiError) {
    const detail = error.detail ?? error.message
    const status = error.status
    const base = { code: error.code, status, detail, traceId: error.traceId }
    if (status === null || status === 504 || (error.code === null && status >= 500)) return view('uncertain', base)
    switch (error.code) {
      case 'CATEGORY_NOT_FOUND':
        return view('not_found', base)
      case 'CATEGORY_CODE_EXISTS':
        return view('code_exists', { ...base, fieldErrors: { code: '该编码已被其他类别使用。' } })
      case 'CATEGORY_IN_USE':
        return view('in_use', base)
      case 'VALIDATION_ERROR':
        return view('validation', { ...base, fieldErrors: validationFields(error.errors, detail) })
      default:
        return view('unknown', base)
    }
  }
  const detail = error instanceof Error && error.message ? error.message : '类别请求失败，请稍后重试。'
  return view('unknown', { detail })
}
