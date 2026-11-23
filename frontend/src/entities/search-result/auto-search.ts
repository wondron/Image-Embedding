/**
 * 业务搜图 — POST /api/search/auto/file | /url (contract §6.6): the self-built library with the whole
 * image; when that finds nothing over the threshold, the regions GroundingDINO finds in it (region
 * search) and, when the server has it on, 菜谱识别 of those regions; 现网 Global as an optional
 * fallback. `{ ok, trace_id, scene, items, fallback, message? }`. Every item is the shared card with
 * its own `source` first: a self-built hit (read like POST /api/search; its `details.outputs_heating`
 * says whether it carries heating information, `details.region` which box of the query image found
 * it), a Global result (read like POST /api/search/global/*), or 菜谱识别's one card (the dish names
 * as its tags, nothing else). The envelope says neither: what the UI shows for the answer as a
 * whole is worked out from the cards.
 */
import { asBoolean, asFiniteNumber, asText, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'
import type {
  AutoSearchFallbackWire,
  AutoSearchHitWire,
  AutoSearchRegionWire,
  AutoSearchResponseWire,
  AutoSearchSelfDetailsWire,
} from '../../shared/api'
import { toApiSceneCode } from '../scene'
import { toPredictImage } from './legacy-predict'
import { readCardFields, toSearchImage } from './mapper'
import type { SearchChannel, SearchFallback, SearchFallbackStatus, SearchImage, SearchRegion, SearchResponse } from './model'

const FALLBACK_STATUSES: readonly SearchFallbackStatus[] = ['not_needed', 'disabled', 'hit', 'empty', 'failed']

type CardSource = 'self' | 'recipe' | 'global'

/** A card's `source`: self | recipe | global; anything else is treated as not sent. */
function parseCardSource(value: unknown): CardSource | undefined {
  const source = asText(value)?.toLowerCase()
  return source === 'self' || source === 'recipe' || source === 'global' ? source : undefined
}

/** Envelope `source` of an older server: a card source, or empty; anything else is treated as not sent. */
function parseEnvelopeSource(value: unknown): CardSource | 'empty' | undefined {
  return asText(value)?.toLowerCase() === 'empty' ? 'empty' : parseCardSource(value)
}

/**
 * The cards summed up, as the UI names an answer: self = self-built hits (菜谱识别's card may
 * follow them), recipe = that card alone, global = the fallback's results, empty = nothing.
 */
function answerSource(items: readonly SearchImage[]): SearchChannel | undefined {
  if (items.length === 0) return 'empty'
  return items.some(item => item.source === 'self') ? 'self' : items[0].source
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

/**
 * `details.region` of a self-built card: the box of the query image whose crop found it (region
 * search). undefined when absent (a whole-image hit) or not a box: score in 0–1, x2 > x1 ≥ 0,
 * y2 > y1 ≥ 0.
 */
function parseRegion(value: unknown): SearchRegion | undefined {
  const region = untrusted<AutoSearchRegionWire>(value)
  if (!region) return undefined
  const score = asFiniteNumber(region.score)
  const box = Array.isArray(region.box) ? region.box.map(asFiniteNumber) : []
  if (score === null || score < 0 || score > 1 || box.length !== 4 || box.some(edge => edge === null)) return undefined
  const [x1, y1, x2, y2] = box as number[]
  if (x1 < 0 || y1 < 0 || x2 <= x1 || y2 <= y1) return undefined
  return { label: asText(region.label) ?? '', score, box: [x1, y1, x2, y2] }
}

/** 业务搜图 body → SearchResponse. `items` must be an array (as for POST /api/search). */
export function toAutoSearchResponse(payload: unknown): SearchResponse {
  const body = untrusted<AutoSearchResponseWire>(payload)
  if (!body || !Array.isArray(body.items)) {
    throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。')
  }
  // An older server said these on the envelope; now each card says them itself
  const envelopeSource = parseEnvelopeSource(body.source)
  const envelopeHeating = asBoolean(body.outputs_heating)
  const scene = asText(body.scene)
  const fallback = parseFallback(body.fallback)
  const items = untrustedList<AutoSearchHitWire>(body.items).map((hit, index) => {
    const channel = parseCardSource(hit.source) ?? (envelopeSource === 'empty' ? undefined : envelopeSource)
    if (channel === 'recipe') return toRecipeImage(hit, index)
    // Global results carry no category or heating information
    if (channel === 'global') return { ...toPredictImage(hit, index), source: 'global' as const, outputsHeating: false }
    const details = untrusted<AutoSearchSelfDetailsWire>(hit.details)
    const outputsHeating = asBoolean(details?.outputs_heating) ?? envelopeHeating
    const image = toSearchImage(hit, index, { source: channel, outputsHeating })
    const region = parseRegion(details?.region)
    return region ? { ...image, region } : image
  })
  const source = answerSource(items)
  const message = source === 'empty' ? emptyMessage(asText(body.message), fallback) : asText(body.message)
  return {
    items,
    traceId: asText(body.trace_id),
    ...(message ? { message } : {}),
    ...(source ? { source } : {}),
    ...(scene ? { scene: toApiSceneCode(scene) } : {}),
    ...(fallback ? { fallback } : {}),
  }
}
