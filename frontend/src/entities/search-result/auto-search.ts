/**
 * 业务搜图 — POST /api/search/auto/file | /url (contract §6.6): the self-built library first, 现网
 * Global as an optional fallback. `{ ok, trace_id, source, scene, outputs_heating, items, fallback,
 * message? }`. items are self-built hits when source=self (read like POST /api/search) and Global
 * results when source=global (read like POST /api/search/global/*): both are the shared card.
 */
import { asBoolean, asText, untrusted, untrustedList } from '../../shared/lib/parse'
import type { AutoSearchFallbackWire, AutoSearchResponseWire, PredictHitWire, SearchHitWire } from '../../shared/api'
import { toApiSceneCode } from '../scene'
import { toPredictImage } from './legacy-predict'
import { toSearchImage } from './mapper'
import type { SearchFallback, SearchFallbackStatus, SearchResponse } from './model'

const FALLBACK_STATUSES: readonly SearchFallbackStatus[] = ['not_needed', 'disabled', 'hit', 'empty', 'failed']

/** Envelope `source`: self | global | empty; anything else is treated as not sent. */
function parseSource(value: unknown): 'self' | 'global' | 'empty' | undefined {
  const source = asText(value)?.toLowerCase()
  return source === 'self' || source === 'global' || source === 'empty' ? source : undefined
}

function parseFallback(value: unknown): SearchFallback | undefined {
  const fallback = untrusted<AutoSearchFallbackWire>(value)
  const status = FALLBACK_STATUSES.find(known => known === fallback?.status)
  if (!status) return undefined
  return { status, error: asText(fallback?.error), detail: asText(fallback?.detail) }
}

/** Why nothing came back; a failed fallback adds its reason and code. */
function emptyMessage(message: string | null, fallback: SearchFallback | undefined): string {
  const said = message ?? '自建库暂未找到相关图片。'
  if (fallback?.status !== 'failed') return said
  const reason = `${fallback.detail ?? ''}${fallback.error ? `（${fallback.error}）` : ''}`
  return reason ? `${said}：${reason}` : said
}

/** 业务搜图 body → SearchResponse. `items` must be an array (as for POST /api/search). */
export function toAutoSearchResponse(payload: unknown): SearchResponse {
  const body = untrusted<AutoSearchResponseWire>(payload)
  if (!body || !Array.isArray(body.items)) {
    throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。')
  }
  const source = parseSource(body.source)
  const outputsHeating = asBoolean(body.outputs_heating)
  const scene = asText(body.scene)
  const fallback = parseFallback(body.fallback)
  const message = source === 'empty' ? emptyMessage(asText(body.message), fallback) : asText(body.message)
  const items = source === 'global'
    // Global results carry no category or heating information
    ? untrustedList<PredictHitWire>(body.items).map((doc, index) => ({
      ...toPredictImage(doc, index), source: 'global' as const, outputsHeating: false,
    }))
    : untrustedList<SearchHitWire>(body.items).map((hit, index) => toSearchImage(hit, index, {
      source: source === 'self' ? 'self' : undefined, outputsHeating,
    }))
  return {
    items,
    traceId: asText(body.trace_id),
    ...(message ? { message } : {}),
    ...(source ? { source } : {}),
    ...(scene ? { scene: toApiSceneCode(scene) } : {}),
    ...(outputsHeating !== undefined ? { outputsHeating } : {}),
    ...(fallback ? { fallback } : {}),
  }
}
