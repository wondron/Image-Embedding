/**
 * 多图搜索 — POST /api/search/batch/files | /urls (contract §6.7): several query images against the
 * self-built library in one request. `{ ok, trace_id, scene, outputs_heating, …, results }`, one
 * entry per query image in request order: its hits (read like POST /api/search), or why that
 * image could not be searched.
 */
import { asBoolean, asFiniteNumber, asInteger, asText, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'
import type { BatchSearchResponseWire, BatchSearchResultWire, SearchHitWire } from '../../shared/api'
import { toApiSceneCode } from '../scene'
import { toSearchImage } from './mapper'
import type { SearchResponse } from './model'

/** One query image of a batch. */
export interface BatchSearchOutcome {
  /** Position in the request, from 0. */
  index: number
  /** The file name (or the link) the server answered for. */
  input: string
  /** What POST /api/search would have answered for this image; null when it was not searched. */
  response: SearchResponse | null
  /** Why it was not searched, ready to show; null when it was. */
  error: string | null
  /** The error code (e.g. SEARCH_BAD_IMAGE); null when it was searched. */
  code: string | null
  /** Seconds the server spent on this image; null when it did not say. */
  elapsed: number | null
}

export interface BatchSearchResponse {
  traceId: string | null
  /** Scene actually searched. */
  scene: string | null
  outputsHeating: boolean | undefined
  outcomes: BatchSearchOutcome[]
}

function toOutcome(
  entry: Untrusted<BatchSearchResultWire> | null,
  position: number,
  envelope: { traceId: string | null; scene: string | null; outputsHeating: boolean | undefined },
): BatchSearchOutcome {
  const index = asInteger(entry?.index) ?? position
  const input = asText(entry?.input) ?? `图片 ${index + 1}`
  const milliseconds = asFiniteNumber(entry?.elapsed_ms)
  const elapsed = milliseconds !== null && milliseconds >= 0 ? milliseconds / 1000 : null
  if (!entry || entry.ok !== true || !Array.isArray(entry.items)) {
    const code = asText(entry?.error)
    const detail = asText(entry?.detail)
    const error = detail && code ? `${detail}（${code}）` : detail ?? code ?? '这张图片没有返回检索结果，请重试。'
    return { index, input, response: null, error, code, elapsed }
  }
  const source = asText(entry.source)?.toLowerCase()
  const channel = source === 'self' || source === 'empty' ? source : undefined
  const message = asText(entry.message) ?? (channel === 'empty' ? '自建库暂未找到相关图片。' : null)
  return {
    index,
    input,
    response: {
      items: untrustedList<SearchHitWire>(entry.items).map((hit, order) => toSearchImage(hit, order, {
        source: channel, outputsHeating: envelope.outputsHeating,
      })),
      traceId: envelope.traceId,
      ...(message ? { message } : {}),
      ...(channel ? { source: channel } : {}),
      ...(envelope.scene ? { scene: envelope.scene } : {}),
      ...(envelope.outputsHeating !== undefined ? { outputsHeating: envelope.outputsHeating } : {}),
    },
    error: null,
    code: null,
    elapsed,
  }
}

/** 多图搜索 body → one outcome per query image. `results` must be an array. */
export function toBatchSearchResponse(payload: unknown): BatchSearchResponse {
  const body = untrusted<BatchSearchResponseWire>(payload)
  if (!body || !Array.isArray(body.results)) {
    throw new Error('多图搜索接口返回的数据格式不正确，请检查后端响应。')
  }
  const sceneCode = asText(body.scene)
  const envelope = {
    traceId: asText(body.trace_id),
    scene: sceneCode ? toApiSceneCode(sceneCode) : null,
    outputsHeating: asBoolean(body.outputs_heating),
  }
  return {
    ...envelope,
    outcomes: body.results.map((entry, position) => toOutcome(untrusted<BatchSearchResultWire>(entry), position, envelope)),
  }
}
