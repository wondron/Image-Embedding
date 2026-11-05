import { readableApiError } from '../../lib/search'

export function apiRoot(baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''): string {
  return baseUrl.replace(/\/+$/, '')
}

export type ApiJsonOptions = {
  method?: string
  body?: BodyInit | null
  headers?: HeadersInit
  signal?: AbortSignal
  action?: string
}

/** Shared fetch + JSON parse with readable errors. Pages must not call fetch directly. */
export async function apiJson<T = unknown>(
  path: string,
  {
    method = 'GET',
    body,
    headers,
    signal,
    action = '请求',
    baseUrl = import.meta.env.VITE_API_BASE_URL ?? '',
  }: ApiJsonOptions & { baseUrl?: string } = {},
): Promise<{ response: Response; payload: T }> {
  const url = path.startsWith('http') ? path : `${apiRoot(baseUrl)}${path.startsWith('/') ? '' : '/'}${path}`
  let response: Response
  try {
    response = await fetch(url, { method, body, headers, signal })
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error
    throw new Error(`无法连接服务，请确认后端已启动，并检查接口地址与网络连接。`)
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error
    if (!response.ok) throw new Error(readableApiError(null, response.status, action))
    throw new Error(`${action}接口未返回有效的 JSON 数据，请检查接口地址与代理配置。`)
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status, action))
  return { response, payload: payload as T }
}

export type PingResult = { ok: boolean; online: boolean }

/** Health check — moved out of App.tsx (R20 / V3 / A2). */
export async function pingApi(baseUrl = '', signal?: AbortSignal): Promise<PingResult> {
  try {
    const controller = signal ?? AbortSignal.timeout(5000)
    const response = await fetch(`${apiRoot(baseUrl)}/api/ping`, { signal: controller })
    if (!response.ok) return { ok: false, online: false }
    const ping = await response.json() as { ok?: boolean }
    const online = ping.ok === true
    return { ok: online, online }
  } catch {
    return { ok: false, online: false }
  }
}