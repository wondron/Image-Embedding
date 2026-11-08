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

function isAbort(error: unknown, signal?: AbortSignal): boolean {
  return Boolean(signal?.aborted) || (error instanceof Error && error.name === 'AbortError')
}

/**
 * The one fetch path for JSON endpoints. A network failure, a non-JSON body and a non-2xx
 * response all become ApiError; an abort is rethrown unchanged. The parsed body comes back
 * unchecked: entity mappers read it field by field.
 */
export async function requestJson(path: string, {
  baseUrl,
  method = 'GET',
  query,
  json,
  form,
  signal,
  labels,
}: RequestJsonOptions): Promise<unknown> {
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
    if (isAbort(error, signal)) throw error
    throw new ApiError(`无法连接${labels.service}，请确认后端已启动，并检查接口地址与网络连接。`)
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    if (isAbort(error, signal)) throw error
    if (!response.ok) throw apiErrorFromResponse(null, response.status, labels.action)
    throw new ApiError(`${labels.endpoint}未返回有效的 JSON 数据，请检查接口地址与代理配置。`, { status: response.status })
  }
  if (!response.ok) throw apiErrorFromResponse(payload, response.status, labels.action)
  return payload
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
