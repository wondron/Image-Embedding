import { ApiError, requestJsonResult, type ApiLabels, type AddEnumValueRequestWire } from '../../shared/api'
import { toEnumDetail, toEnumList, type EnumDetailResult, type EnumListResult } from './model'
import type { AppendPayload } from './policy'

const LABELS: ApiLabels = { service: '枚举服务', endpoint: '枚举接口', action: '枚举配置' }

export interface EnumRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

export type AddEnumValueResult =
  | { outcome: 'created'; item: EnumDetailResult['item']; traceId: string | null; status: number }
  | { outcome: 'created_unparsed'; traceId: string | null; status: number }

function enumPath(name: string, suffix = ''): string {
  return `/api/enums/${encodeURIComponent(name)}${suffix}`
}

export async function listEnums({ baseUrl, signal }: EnumRequestOptions = {}): Promise<EnumListResult> {
  const result = await requestJsonResult('/api/enums', { baseUrl, signal, labels: LABELS })
  const parsed = toEnumList(result.body, result.traceId)
  if (!parsed) {
    throw new ApiError('枚举接口未返回有效的枚举列表。', { status: result.status, traceId: result.traceId })
  }
  return parsed
}

export async function getEnum(name: string, { baseUrl, signal }: EnumRequestOptions = {}): Promise<EnumDetailResult> {
  const result = await requestJsonResult(enumPath(name), { baseUrl, signal, labels: LABELS })
  const parsed = toEnumDetail(result.body, result.traceId)
  if (!parsed) {
    throw new ApiError('枚举接口未返回有效的枚举详情。', { status: result.status, traceId: result.traceId })
  }
  return parsed
}

/**
 * POST /api/enums/{name}/values. The body is exactly { value, label, description }.
 * HTTP 201 (or another 2xx) is a successful write even when the body cannot be parsed.
 * This function does not retry.
 */
export async function addEnumValue(
  name: string,
  payload: AppendPayload,
  { baseUrl, signal }: EnumRequestOptions = {},
): Promise<AddEnumValueResult> {
  const body: AddEnumValueRequestWire = {
    value: payload.value,
    label: payload.label,
    description: payload.description,
  }
  try {
    const result = await requestJsonResult(enumPath(name, '/values'), {
      baseUrl,
      method: 'POST',
      json: body,
      signal,
      labels: LABELS,
    })
    const parsed = toEnumDetail(result.body, result.traceId)
    if (!parsed) return { outcome: 'created_unparsed', traceId: result.traceId, status: result.status }
    return { outcome: 'created', item: parsed.item, traceId: parsed.traceId, status: result.status }
  } catch (error) {
    if (error instanceof ApiError && error.status !== null && error.status >= 200 && error.status < 300) {
      return { outcome: 'created_unparsed', traceId: error.traceId, status: error.status }
    }
    throw error
  }
}
