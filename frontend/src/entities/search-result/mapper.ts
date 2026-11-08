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
import type { SearchHitWire, SearchResponseWire } from '../../shared/api'
import {
  parseDescription,
  parseFoodState,
  parseHeatingGroup,
  parseLabelStatus,
  parseSceneCodes,
} from '../image'
import { toApiSceneCode } from '../scene'
import { displayCategoryLabel, type SearchImage, type SearchResponse } from './model'

/** Finite score in [-1, 1]; anything else (missing, string, 80 …) is "not returned", never 0. */
export function parseSimilarity(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= -1 && value <= 1 ? value : null
}

/** Envelope `source`: exactly self | empty (§6.1.1); anything else is treated as not sent. */
function parseSource(value: unknown): 'self' | 'empty' | undefined {
  const source = asText(value)?.toLowerCase()
  return source === 'self' || source === 'empty' ? source : undefined
}

function toSearchImage(
  hit: Untrusted<SearchHitWire>,
  index: number,
  envelope: { source?: 'self' | 'empty'; outputsHeating?: boolean },
): SearchImage {
  const category = asText(hit.category)
  const sceneLabel = asText(hit.scene_label)
  return {
    id: asId(hit.id) ?? `image-${index + 1}`,
    title: displayCategoryLabel({ sceneLabel, category }) ?? `未命名图片 ${index + 1}`,
    imageUrl: safeHttpUrl(hit.original_url),
    thumbnailUrl: safeHttpUrl(hit.thumb_url),
    alternateUrls: [],
    similarity: parseSimilarity(hit.similarity),
    categoryId: asInteger(hit.category_id),
    category,
    scenes: parseSceneCodes(hit.scenes),
    sceneLabel,
    labelStatus: parseLabelStatus(hit.label_status),
    description: parseDescription(hit.description),
    foodState: parseFoodState(hit.food_state),
    heatingGroup: parseHeatingGroup(hit.heating_group),
    width: asPositiveNumber(hit.width),
    height: asPositiveNumber(hit.height),
    ...(envelope.outputsHeating !== undefined ? { outputsHeating: envelope.outputsHeating } : {}),
    ...(envelope.source === 'self' ? { source: 'self' as const } : {}),
    pageUrl: null,
    siteName: null,
    snippets: [],
  }
}

/**
 * POST /api/search body (§6.1.1) → SearchResponse. Reads only the contract's fields:
 * `items` must be an array (P3/P4), and FlyB-era keys (`result`, `image_url`, `details` …) are never read.
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
