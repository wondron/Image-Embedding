import { DEFAULT_SCENE, toApiSceneCode } from '../entities/scene'
import { normalizeLibraryItem, type LibraryImageItem } from '../entities/image'
import { readableApiError } from './search'

export type { LibraryImageItem }

type ObjectValue = Record<string, unknown>

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : {}
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function positiveInt(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return Math.floor(value)
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    const n = Math.floor(Number(value))
    return n >= 0 ? n : fallback
  }
  return fallback
}

export interface ListImagesResponse {
  ok: boolean
  traceId: string | null
  scene: string
  page: number
  pageSize: number
  total: number
  items: LibraryImageItem[]
  raw: Record<string, unknown>
}

export interface ListImagesOptions {
  page?: number
  pageSize?: number
  q?: string | null
  category?: string | null
  /** Default general (PG v0.1). Do not omit to mean "all scenes". */
  scene?: string
  baseUrl?: string
  signal?: AbortSignal
  traceId?: string | null
}

/** Thin adapter for GET /api/images success body. */
export function normalizeListImagesResponse(payload: unknown): ListImagesResponse {
  const body = object(payload)
  const rows = Array.isArray(body.items) ? body.items : []
  return {
    ok: body.ok === true || (body.ok !== false && rows.length >= 0),
    traceId: text(body.trace_id),
    scene: toApiSceneCode(body.scene, DEFAULT_SCENE),
    page: positiveInt(body.page, 1) || 1,
    pageSize: positiveInt(body.page_size, 24) || 24,
    total: positiveInt(body.total, 0),
    items: rows
      .filter((row): row is ObjectValue => row !== null && typeof row === 'object' && !Array.isArray(row))
      .map(normalizeLibraryItem)
      .filter((row): row is LibraryImageItem => row !== null),
    raw: body,
  }
}

/** Build query: omit empty q / 「全部类别」; always page_size=24; scene defaults general. */
export function buildListImagesQuery({
  page = 1,
  pageSize = 24,
  q,
  category,
  scene = DEFAULT_SCENE,
  traceId,
}: Pick<ListImagesOptions, 'page' | 'pageSize' | 'q' | 'category' | 'scene' | 'traceId'>): URLSearchParams {
  const params = new URLSearchParams()
  params.set('page', String(Math.max(1, Math.floor(page) || 1)))
  params.set('page_size', String(pageSize > 0 ? Math.floor(pageSize) : 24))
  params.set('scene', toApiSceneCode(scene, DEFAULT_SCENE))
  const trimmedQ = typeof q === 'string' ? q.trim() : ''
  if (trimmedQ) params.set('q', trimmedQ)
  const trimmedCategory = typeof category === 'string' ? category.trim() : ''
  if (trimmedCategory && trimmedCategory !== '全部类别') params.set('category', trimmedCategory)
  if (traceId?.trim()) params.set('trace_id', traceId.trim())
  return params
}

/** List library images via GET /api/images. */
export async function listImages({
  page = 1,
  pageSize = 24,
  q,
  category,
  scene = DEFAULT_SCENE,
  baseUrl = import.meta.env.VITE_API_BASE_URL ?? '',
  signal,
  traceId,
}: ListImagesOptions = {}): Promise<ListImagesResponse> {
  const params = buildListImagesQuery({ page, pageSize, q, category, scene, traceId })
  const root = baseUrl.replace(/\/+$/, '')
  const url = `${root}/api/images?${params.toString()}`

  let response: Response
  try {
    response = await fetch(url, { method: 'GET', signal })
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error
    throw new Error('无法连接图库服务，请确认后端已启动，并检查接口地址与网络连接。')
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error
    if (!response.ok) throw new Error(readableApiError(null, response.status, '图库列表'))
    throw new Error('图库接口未返回有效的 JSON 数据，请检查接口地址与代理配置。')
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status, '图库列表'))

  return normalizeListImagesResponse(payload)
}