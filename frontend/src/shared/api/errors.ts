import { asObject, asText, untrusted } from '../lib/parse'
import type { ErrorResponseWire } from './wire'

/** A failed API call. `message` is ready to show; the other fields are for logs and branching. */
export class ApiError extends Error {
  /** HTTP status; null when no response arrived. */
  readonly status: number | null
  /** Wire `error` code (e.g. SEARCH_BAD_IMAGE); null when the body had none. */
  readonly code: string | null
  /** Wire `trace_id`, to quote when reporting the failure. */
  readonly traceId: string | null

  constructor(message: string, { status = null, code = null, traceId = null }: {
    status?: number | null
    code?: string | null
    traceId?: string | null
  } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.traceId = traceId
  }
}

/**
 * Human-readable text for a failed response. Reads the backend envelope
 * ({ok:false, error, detail, trace_id}) and, from proxies or older servers,
 * FastAPI's default `detail` list or a `{ message }` detail.
 */
export function readableApiError(payload: unknown, status: number, action = '搜索'): string {
  const body = asObject(payload) ?? {}
  const code = asText(body.error)
  const detail = body.detail ?? body.message
  if (Array.isArray(detail)) {
    const errors = detail.map(entry => {
      const value = asObject(entry) ?? {}
      const message = asText(value.msg)
      if (!message) return null
      const location = Array.isArray(value.loc)
        ? value.loc.filter(part => typeof part === 'string' || typeof part === 'number').join('.')
        : ''
      return location ? `${location}: ${message}` : message
    }).filter(Boolean)
    if (errors.length) return `请求参数错误（HTTP ${status}）：${errors.join('；')}`
  }
  const description = asText(detail) ?? asText(asObject(detail)?.message)
  if (description && code) return `${action}失败（HTTP ${status} · ${code}）：${description}`
  if (description) return `${action}失败（HTTP ${status}）：${description}`
  if (code) return `${action}失败（HTTP ${status}）：${code}`
  if (status === 503) return `${action}服务尚未就绪（HTTP 503），请检查后端服务和模型状态。`
  if (status === 413) return '图片过大（HTTP 413），请压缩图片后重试。'
  if (status === 504) return `${action}超时（HTTP 504），请稍后重试。`
  return `${action}失败（HTTP ${status}），请检查后端服务后重试。`
}

/** ApiError for a non-2xx response; `payload` is the parsed body, or null when it was not JSON. */
export function apiErrorFromResponse(payload: unknown, status: number, action: string): ApiError {
  const body = untrusted<ErrorResponseWire>(payload)
  return new ApiError(readableApiError(payload, status, action), {
    status,
    code: asText(body?.error),
    traceId: asText(body?.trace_id),
  })
}
