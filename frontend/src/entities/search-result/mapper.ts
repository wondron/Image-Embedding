import {
  asBoolean,
  asId,
  asInteger,
  asPositiveNumber,
  asText,
  untrusted,
  untrustedList,
  type Untrusted,
} from '../../shared/lib/parse'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import type {
  SearchHitBaseWire,
  SearchHitDetailsWire,
  SearchHitImageWire,
  SearchHitWire,
  SearchResponseWire,
} from '../../shared/api'
import {
  parseDescription,
  parseFoodState,
  parseHeatingGroup,
  parseLabelStatus,
  parseSceneCodes,
} from '../image'
import { toApiSceneCode } from '../scene'
import { displayCategoryLabel, type SearchImage, type SearchResponse } from './model'

/** Cosine scores can overshoot their bounds slightly after FP32 computation. */
const SIMILARITY_EPSILON = 1e-6

/** Finite score in [-1, 1], allowing FP32 roundoff; invalid/missing scores stay "not returned". */
export function parseSimilarity(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < -1 - SIMILARITY_EPSILON || value > 1 + SIMILARITY_EPSILON) return null
  return Math.min(1, Math.max(-1, value))
}

/** The four top-level fields of a result card, read the same way for both channels (§6.1.1). */
export function readCardFields(hit: Untrusted<SearchHitBaseWire<unknown>>): Pick<SearchImage, 'tags' | 'description' | 'imageUrl' | 'similarity'> {
  return {
    tags: asText(hit.tags) ?? '',
    description: parseDescription(hit.description),
    imageUrl: safeHttpUrl(hit.original_url),
    similarity: parseSimilarity(hit.similarity),
  }
}

/** Envelope `source`: exactly self | empty (§6.1.1); anything else is treated as not sent. */
function parseSource(value: unknown): 'self' | 'empty' | undefined {
  const source = asText(value)?.toLowerCase()
  return source === 'self' || source === 'empty' ? source : undefined
}

/**
 * One self-built hit (POST /api/search, and 业务搜图 with source=self) → SearchImage. `envelope`:
 * the hit's channel and heating flag, which POST /api/search says once for all hits on its
 * envelope and 业务搜图 on each card.
 */
export function toSearchImage(
  hit: Untrusted<SearchHitWire>,
  index: number,
  envelope: { source?: 'self' | 'empty'; outputsHeating?: boolean },
): SearchImage {
  const card = readCardFields(hit)
  const details = untrusted<SearchHitDetailsWire>(hit.details) ?? {}
  const image = untrustedList<SearchHitImageWire>(details.images)[0] ?? {}
  const category = asText(details.category)
  const sceneLabel = asText(details.scene_label)
  return {
    ...card,
    id: asId(details.id) ?? `image-${index + 1}`,
    title: displayCategoryLabel({ sceneLabel, category }) ?? (card.tags || `未命名图片 ${index + 1}`),
    thumbnailUrl: safeHttpUrl(image.thumbnail_url),
    alternateUrls: [],
    categoryId: asInteger(details.category_id),
    category,
    scenes: parseSceneCodes(details.scenes),
    sceneLabel,
    labelStatus: parseLabelStatus(details.label_status),
    foodState: parseFoodState(details.food_state),
    heatingGroup: parseHeatingGroup(details.heating_group),
    width: asPositiveNumber(image.width),
    height: asPositiveNumber(image.height),
    ...(envelope.outputsHeating !== undefined ? { outputsHeating: envelope.outputsHeating } : {}),
    ...(envelope.source === 'self' ? { source: 'self' as const } : {}),
    pageUrl: null,
    siteName: null,
  }
}

/**
 * POST /api/search body (§6.1.1) → SearchResponse. `items` must be an array (P3/P4); each hit is
 * the shared result card (top-level card fields, the rest under `details`).
 */
export function toSearchResponse(payload: unknown): SearchResponse {
  const body = untrusted<SearchResponseWire>(payload)
  if (!body || !Array.isArray(body.items)) {
    throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。')
  }
  const source = parseSource(body.source)
  const outputsHeating = asBoolean(body.outputs_heating)
  const scene = asText(body.scene)
  const message = asText(body.message) ?? (source === 'empty' ? '自建库暂未找到相关图片。' : null)
  return {
    items: untrustedList<SearchHitWire>(body.items).map((hit, index) => toSearchImage(hit, index, { source, outputsHeating })),
    traceId: asText(body.trace_id),
    ...(message ? { message } : {}),
    ...(source ? { source } : {}),
    ...(scene ? { scene: toApiSceneCode(scene) } : {}),
    ...(outputsHeating !== undefined ? { outputsHeating } : {}),
  }
}
