import { asText, untrusted } from '../lib/parse'
import { ApiError, apiErrorFromResponse } from './errors'

/** API origin without a trailing slash; '' means same origin (the Vite dev proxy). */
export function apiRoot(baseUrl: string = import.meta.env.VITE_API_BASE_URL ?? ''): string {
  return baseUrl.replace(/\/+$/, '')
}

/** How one endpoint names itself in failure messages. */
export interface ApiLabels {
  /** 「无法连接{service}…」, e.g. 图库服务 */
  service: string
  /** 「{endpoint}未返回有效的 JSON 数据…」, e.g. 图库接口 */
  endpoint: string
  /** 「{action}失败（HTTP …）」, e.g. 图库列表 */
  action: string
}

export interface RequestJsonOptions {
  /** API origin; defaults to VITE_API_BASE_URL. */
  baseUrl?: string
  method?: 'GET' | 'POST'
  query?: URLSearchParams
  /** JSON body, sent with Content-Type: application/json. */
  json?: unknown
  /** Multipart body; the browser sets the boundary header. */
  form?: FormData
  signal?: AbortSignal
  labels: ApiLabels
}

function abortedError(error: unknown, signal?: AbortSignal): unknown | null {
  const namedAbort = error instanceof DOMException && (error.name === 'AbortError' || error.name === 'TimeoutError')
  if (!signal?.aborted && !namedAbort) return null
  const reason = signal?.reason
  if (reason instanceof Error) return reason
  return error instanceof Error ? error : new DOMException('Aborted', 'AbortError')
}

function headerTrace(response: Response): string | null {
  const value = response.headers.get('X-Trace-Id')
  const trimmed = value?.trim()
  return trimmed || null
}

/**
 * Parsed JSON plus the HTTP status and trace id.
 * A network failure, a non-JSON body and a non-2xx response become ApiError;
 * an abort is rethrown. Entity mappers still read the body field by field.
 */
export interface JsonResult {
  body: unknown
  status: number
  /** Body `trace_id` when present, otherwise response header `X-Trace-Id`. */
  traceId: string | null
}

/**
 * Same path as requestJson, plus status and trace id.
 * Success enum bodies omit trace_id, so the header is the trace source.
 */
export async function requestJsonResult(path: string, {
  baseUrl,
  method = 'GET',
  query,
  json,
  form,
  signal,
  labels,
}: RequestJsonOptions): Promise<JsonResult> {
  const search = query?.toString()
  const url = `${apiRoot(baseUrl)}${path}${search ? `?${search}` : ''}`
  const init: RequestInit = { method, signal }
  if (form) {
    init.body = form
  } else if (json !== undefined) {
    init.body = JSON.stringify(json)
    init.headers = { 'Content-Type': 'application/json' }
  }

  let response: Response
  try {
    response = await fetch(url, init)
  } catch (error) {
    const aborted = abortedError(error, signal)
    if (aborted) throw aborted
    throw new ApiError(`无法连接${labels.service}，请确认后端已启动，并检查接口地址与网络连接。`)
  }

  const traceHeader = headerTrace(response)
  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    const aborted = abortedError(error, signal)
    if (aborted) throw aborted
    if (!response.ok) throw apiErrorFromResponse(null, response.status, labels.action, traceHeader)
    throw new ApiError(`${labels.endpoint}未返回有效的 JSON 数据，请检查接口地址与代理配置。`, {
      status: response.status,
      traceId: traceHeader,
    })
  }
  if (!response.ok) throw apiErrorFromResponse(payload, response.status, labels.action, traceHeader)
  const bodyTrace = asText(untrusted<{ trace_id?: unknown }>(payload)?.trace_id)
  return { body: payload, status: response.status, traceId: bodyTrace ?? traceHeader }
}

export async function requestJson(path: string, options: RequestJsonOptions): Promise<unknown> {
  const result = await requestJsonResult(path, options)
  return result.body
}

export type PingResult = { ok: boolean; online: boolean }

/** Health check (GET /api/ping): online only when the body says ok=true. */
export async function pingApi(baseUrl = '', signal?: AbortSignal): Promise<PingResult> {
  try {
    const response = await fetch(`${apiRoot(baseUrl)}/api/ping`, { signal: signal ?? AbortSignal.timeout(5000) })
    if (!response.ok) return { ok: false, online: false }
    const ping = await response.json() as { ok?: unknown }
    const online = ping.ok === true
    return { ok: online, online }
  } catch {
    return { ok: false, online: false }
  }
}
