import { requestJson, type ApiLabels, type PredictUrlBodyWire, type SearchJsonRequestWire } from '../../shared/api'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import { DEFAULT_SCENE, toApiSceneCode } from '../scene'
import { fromPredictResponse } from './legacy-predict'
import { toSearchResponse } from './mapper'
import type { SearchResponse } from './model'

const GLOBAL_LABELS: ApiLabels = { service: '搜索服务', endpoint: '搜索接口', action: '搜索' }
const LOCAL_LABELS: ApiLabels = { service: '自建检索服务', endpoint: '自建检索接口', action: '搜索' }

export interface SearchOptions {
  file?: File
  url?: string
  baseUrl?: string
  signal?: AbortSignal
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

/** 现网 Global: POST /api/predict/searchImage_file (multipart `file`) or searchImage_url (JSON `{ img_url }`). */
export async function searchGlobalImages({ file, url, baseUrl, signal }: SearchOptions): Promise<SearchResponse> {
  const query = searchQuery({ file, url })
  let payload: unknown
  if (query.kind === 'file') {
    const form = new FormData()
    form.append('file', query.file)
    payload = await requestJson('/api/predict/searchImage_file', { baseUrl, method: 'POST', form, signal, labels: GLOBAL_LABELS })
  } else {
    const body: PredictUrlBodyWire = { img_url: query.url }
    payload = await requestJson('/api/predict/searchImage_url', { baseUrl, method: 'POST', json: body, signal, labels: GLOBAL_LABELS })
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
