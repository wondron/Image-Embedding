/**
 * 业务搜图 — POST /api/search/auto/file | /url (contract §6.6): the self-built library and, when the
 * server has it on, 菜谱识别 side by side; 现网 Global as an optional fallback. `{ ok, trace_id, source,
 * scene, outputs_heating, items, fallback, message? }`. Every item is the shared card with its own
 * `source`: a self-built hit (read like POST /api/search), a Global result (read like POST
 * /api/search/global/*), or 菜谱识别's one card (the dish names as its tags, nothing else).
 */
import { asBoolean, asText, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'
import type { AutoSearchFallbackWire, AutoSearchHitWire, AutoSearchResponseWire } from '../../shared/api'
import { toApiSceneCode } from '../scene'
import { toPredictImage } from './legacy-predict'
import { readCardFields, toSearchImage } from './mapper'
import type { SearchFallback, SearchFallbackStatus, SearchImage, SearchResponse } from './model'

const FALLBACK_STATUSES: readonly SearchFallbackStatus[] = ['not_needed', 'disabled', 'hit', 'empty', 'failed']

type CardSource = 'self' | 'recipe' | 'global'

/** A card's `source`: self | recipe | global; anything else is treated as not sent. */
function parseCardSource(value: unknown): CardSource | undefined {
  const source = asText(value)?.toLowerCase()
  return source === 'self' || source === 'recipe' || source === 'global' ? source : undefined
}

/** Envelope `source`: a card source, or empty; anything else is treated as not sent. */
function parseSource(value: unknown): CardSource | 'empty' | undefined {
  return asText(value)?.toLowerCase() === 'empty' ? 'empty' : parseCardSource(value)
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

/**
 * 菜谱识别's card → SearchImage: the dish names recognised in the query image are its tags and its
 * title. It stands for no image: no URLs, no score, no category.
 */
function toRecipeImage(hit: Untrusted<AutoSearchHitWire>, index: number): SearchImage {
  const card = readCardFields(hit)
  return {
    ...card,
    id: `recipe-${index + 1}`,
    title: card.tags || '未识别到菜名',
    imageUrl: null,
    thumbnailUrl: null,
    alternateUrls: [],
    similarity: null,
    categoryId: null,
    category: null,
    scenes: [],
    sceneLabel: null,
    labelStatus: null,
    foodState: null,
    heatingGroup: null,
    width: null,
    height: null,
    outputsHeating: false,
    source: 'recipe',
    pageUrl: null,
    siteName: null,
  }
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
  const items = untrustedList<AutoSearchHitWire>(body.items).map((hit, index) => {
    // Each card says where it came from; a server older than 菜谱识别 says it on the envelope only
    const channel = parseCardSource(hit.source) ?? (source === 'empty' ? undefined : source)
    if (channel === 'recipe') return toRecipeImage(hit, index)
    // Global results carry no category or heating information
    if (channel === 'global') return { ...toPredictImage(hit, index), source: 'global' as const, outputsHeating: false }
    return toSearchImage(hit, index, { source: channel, outputsHeating })
  })
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
