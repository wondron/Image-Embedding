import {
  requestJson,
  type ApiLabels,
  type AutoSearchUrlRequestWire,
  type PredictUrlBodyWire,
  type SearchJsonRequestWire,
} from '../../shared/api'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import { DEFAULT_SCENE, toApiSceneCode } from '../scene'
import { toAutoSearchResponse } from './auto-search'
import { toBatchSearchResponse, type BatchSearchResponse } from './batch-search'
import { fromPredictResponse } from './legacy-predict'
import { toSearchResponse } from './mapper'
import type { SearchResponse } from './model'

const GLOBAL_LABELS: ApiLabels = { service: '搜索服务', endpoint: '搜索接口', action: '搜索' }
const LOCAL_LABELS: ApiLabels = { service: '自建检索服务', endpoint: '自建检索接口', action: '搜索' }
const AUTO_LABELS: ApiLabels = { service: '业务搜图服务', endpoint: '业务搜图接口', action: '搜索' }
const BATCH_LABELS: ApiLabels = { service: '自建检索服务', endpoint: '多图搜索接口', action: '多图搜索' }

/** 多图搜索: query images one request may carry (the server's BATCH_MAX_IMAGES). */
export const BATCH_SEARCH_MAX_IMAGES = 30

/**
 * 业务搜图 result count (`top_k`): one number for both channels — the self-built top-K over the
 * threshold, or how many results the Global fallback asks for — so it stops at Global's 20.
 */
export const AUTO_TOP_K_MIN = 1
export const AUTO_TOP_K_MAX = 20
export const AUTO_TOP_K_DEFAULT = 10

/**
 * 现网 Global result count (`search_num` → FlyB DocCount). FlyB takes 1–20 (the server checks its
 * FLAB_CONFIG.DOC_COUNT default against the same range, but not a request's search_num); the
 * default here is that configured DOC_COUNT. Results can be fewer: FlyB may find less, and the
 * similarity filter (GLOBAL_FILTER) drops some when it is on.
 */
export const SEARCH_NUM_MIN = 1
export const SEARCH_NUM_MAX = 20
export const SEARCH_NUM_DEFAULT = 20

export interface SearchOptions {
  file?: File
  url?: string
  baseUrl?: string
  signal?: AbortSignal
  /** Global only: SEARCH_NUM_MIN..SEARCH_NUM_MAX; omitted = the server's DOC_COUNT. */
  searchNum?: number
  /** 自建 only: 1..50, server default 10. */
  topK?: number
  /** 自建 only: null/undefined = server default 0.55; 0 = reject off. */
  scoreThreshold?: number | null
  traceId?: string | null
  /** 自建 only: PG v0.1 scene code; default general. Never sends legacy normal/device. */
  scene?: string
}

type SearchQuery = { kind: 'file'; file: File } | { kind: 'url'; url: string }

/** Exactly one query: a non-empty file, or an HTTP(S) image link. */
function searchQuery({ file, url }: Pick<SearchOptions, 'file' | 'url'>): SearchQuery {
  if ((!file && !url?.trim()) || (file && url?.trim())) {
    throw new Error('请选择一张图片，或填写一个图片链接。')
  }
  if (file) {
    if (!file.size) throw new Error('图片文件为空，请重新选择。')
    return { kind: 'file', file }
  }
  const imageUrl = safeHttpUrl(url)
  if (!imageUrl) throw new Error('请输入有效的 HTTP 或 HTTPS 图片链接。')
  return { kind: 'url', url: imageUrl }
}

/**
 * 现网 Global: POST /api/search/global/file (multipart `file`, `search_num` in the query: the
 * file route reads it only there) or /api/search/global/url (JSON `{ img_url, search_num }`).
 */
export async function searchGlobalImages({ file, url, baseUrl, signal, searchNum }: SearchOptions): Promise<SearchResponse> {
  const query = searchQuery({ file, url })
  if (searchNum !== undefined && !(Number.isInteger(searchNum) && searchNum >= SEARCH_NUM_MIN && searchNum <= SEARCH_NUM_MAX)) {
    throw new Error(`返回数量需为 ${SEARCH_NUM_MIN}–${SEARCH_NUM_MAX} 的整数。`)
  }
  let payload: unknown
  if (query.kind === 'file') {
    const form = new FormData()
    form.append('file', query.file)
    const params = searchNum !== undefined ? new URLSearchParams({ search_num: String(searchNum) }) : undefined
    payload = await requestJson('/api/search/global/file', { baseUrl, method: 'POST', query: params, form, signal, labels: GLOBAL_LABELS })
  } else {
    const body: PredictUrlBodyWire = { img_url: query.url, ...(searchNum !== undefined ? { search_num: searchNum } : {}) }
    payload = await requestJson('/api/search/global/url', { baseUrl, method: 'POST', json: body, signal, labels: GLOBAL_LABELS })
  }
  return fromPredictResponse(payload)
}

/** 自建: POST /api/search — multipart `file` + fields, or JSON SearchJsonRequest `{ img_url, … }` (§3.2 / §6.1). */
export async function searchLocalImages({
  file, url, baseUrl, signal, topK, scoreThreshold, traceId, scene,
}: SearchOptions): Promise<SearchResponse> {
  const apiScene = toApiSceneCode(scene, DEFAULT_SCENE)
  const query = searchQuery({ file, url })
  const threshold = scoreThreshold ?? undefined
  let payload: unknown
  if (query.kind === 'file') {
    const form = new FormData()
    form.append('file', query.file)
    if (topK !== undefined) form.append('top_k', String(topK))
    if (threshold !== undefined) form.append('score_threshold', String(threshold))
    if (traceId) form.append('trace_id', traceId)
    form.append('scene', apiScene)
    payload = await requestJson('/api/search', { baseUrl, method: 'POST', form, signal, labels: LOCAL_LABELS })
  } else {
    const body: SearchJsonRequestWire = {
      img_url: query.url,
      scene: apiScene,
      ...(topK !== undefined ? { top_k: topK } : {}),
      ...(threshold !== undefined ? { score_threshold: threshold } : {}),
      ...(traceId ? { trace_id: traceId } : {}),
    }
    payload = await requestJson('/api/search', { baseUrl, method: 'POST', json: body, signal, labels: LOCAL_LABELS })
  }
  return toSearchResponse(payload)
}

export interface BatchSearchOptions extends Pick<SearchOptions, 'baseUrl' | 'signal' | 'topK' | 'scoreThreshold' | 'traceId' | 'scene'> {
  /** 1..BATCH_SEARCH_MAX_IMAGES query images; they share the scene, top_k and the threshold. */
  files: readonly File[]
}

/**
 * 多图搜索 (自建库): POST /api/search/batch/files — multipart `files` (repeated) + the shared
 * fields. One outcome per file, in the order given: an image that cannot be searched has its own
 * error and does not fail the others (§6.7).
 */
export async function searchLocalBatch({
  files, baseUrl, signal, topK, scoreThreshold, traceId, scene,
}: BatchSearchOptions): Promise<BatchSearchResponse> {
  if (!files.length) throw new Error('请至少选择一张图片。')
  if (files.length > BATCH_SEARCH_MAX_IMAGES) throw new Error(`一次最多搜索 ${BATCH_SEARCH_MAX_IMAGES} 张图片。`)
  const threshold = scoreThreshold ?? undefined
  const form = new FormData()
  for (const file of files) form.append('files', file)
  form.append('scene', toApiSceneCode(scene, DEFAULT_SCENE))
  if (topK !== undefined) form.append('top_k', String(topK))
  if (threshold !== undefined) form.append('score_threshold', String(threshold))
  if (traceId) form.append('trace_id', traceId)
  const payload = await requestJson('/api/search/batch/files', { baseUrl, method: 'POST', form, signal, labels: BATCH_LABELS })
  return toBatchSearchResponse(payload)
}

export interface AutoSearchOptions extends Omit<SearchOptions, 'searchNum' | 'topK'> {
  /** AUTO_TOP_K_MIN..AUTO_TOP_K_MAX; omitted = the server's 10. */
  topK?: number
  /** Ask 现网 Global when the self-built library has nothing; omitted = the server's false. */
  globalFallback?: boolean
  /** The caller's device, for the server logs. */
  machineInfo?: string | null
}

/**
 * 业务搜图: POST /api/search/auto/file (multipart `file` + the other fields as form fields) or
 * /api/search/auto/url (JSON `{ image_url, … }`; the server refuses unknown fields). §6.6.
 */
export async function searchAutoImages({
  file, url, baseUrl, signal, topK, scoreThreshold, traceId, scene, globalFallback, machineInfo,
}: AutoSearchOptions): Promise<SearchResponse> {
  const query = searchQuery({ file, url })
  if (topK !== undefined && !(Number.isInteger(topK) && topK >= AUTO_TOP_K_MIN && topK <= AUTO_TOP_K_MAX)) {
    throw new Error(`返回数量需为 ${AUTO_TOP_K_MIN}–${AUTO_TOP_K_MAX} 的整数。`)
  }
  const apiScene = toApiSceneCode(scene, DEFAULT_SCENE)
  const threshold = scoreThreshold ?? undefined
  let payload: unknown
  if (query.kind === 'file') {
    const form = new FormData()
    form.append('file', query.file)
    form.append('scene', apiScene)
    if (topK !== undefined) form.append('top_k', String(topK))
    if (threshold !== undefined) form.append('score_threshold', String(threshold))
    if (globalFallback !== undefined) form.append('global_fallback', String(globalFallback))
    if (traceId) form.append('trace_id', traceId)
    if (machineInfo) form.append('machine_info', machineInfo)
    payload = await requestJson('/api/search/auto/file', { baseUrl, method: 'POST', form, signal, labels: AUTO_LABELS })
  } else {
    const body: AutoSearchUrlRequestWire = {
      image_url: query.url,
      scene: apiScene,
      ...(topK !== undefined ? { top_k: topK } : {}),
      ...(threshold !== undefined ? { score_threshold: threshold } : {}),
      ...(globalFallback !== undefined ? { global_fallback: globalFallback } : {}),
      ...(traceId ? { trace_id: traceId } : {}),
      ...(machineInfo ? { machine_info: machineInfo } : {}),
    }
    payload = await requestJson('/api/search/auto/url', { baseUrl, method: 'POST', json: body, signal, labels: AUTO_LABELS })
  }
  return toAutoSearchResponse(payload)
}
