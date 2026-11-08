/**
 * 现网 Global search — POST /api/predict/searchImage_* — a FlyB passthrough outside contract §6.1:
 * `{ ok, trace_id, result }`, where `result` is a document list or an upstream status envelope
 * (`{ results, status, upstream_error, request_id }`). Kept apart from the §6.1 mapper so these
 * legacy fields (image_url size maps, details.*, similiar …) never leak into self-built search.
 */
import {
  asId,
  asObject,
  asPositiveNumber,
  asText,
  asTextList,
  untrusted,
  untrustedList,
  type JsonObject,
  type Untrusted,
} from '../../shared/lib/parse'
import { safeHttpUrl, uniqueHttpUrls } from '../../shared/lib/safeHttpUrl'
import type {
  PredictDocDetailsWire,
  PredictDocImageWire,
  PredictDocWire,
  PredictResponseWire,
  PredictUpstreamEnvelopeWire,
} from '../../shared/api'
import { parseSimilarity } from './mapper'
import type { SearchChannel, SearchImage, SearchResponse } from './model'

const THUMBNAIL_SIZES = ['large', 'medium', 'small']

/** A size map, or {} for a plain string / anything else. */
function sizeMap(value: unknown): JsonObject {
  return asObject(value) ?? {}
}

/**
 * Thumbnail candidates across every collection (image_url size map, thumbnail_url, details.images[]),
 * ranked by size first: every `large`, then every `medium`, then `small`, then other keys.
 * A plain-string thumbnail counts as large. Entries are unchecked; callers filter them.
 */
function rankedThumbnails(doc: Untrusted<PredictDocWire>, images: Untrusted<PredictDocImageWire>[]): unknown[] {
  const collections = [sizeMap(doc.image_url), doc.thumbnail_url, ...images.map(image => image.thumbnail_url)]
  const ranked: unknown[] = []
  for (const size of THUMBNAIL_SIZES) {
    for (const value of collections) {
      ranked.push(typeof value === 'string' && size === 'large' ? value : sizeMap(value)[size])
    }
  }
  for (const value of collections) {
    ranked.push(...Object.entries(sizeMap(value)).filter(([key]) => !THUMBNAIL_SIZES.includes(key)).map(([, url]) => url))
  }
  return ranked
}

/** Legacy per-document channel; `empty` is never a card channel. */
function documentChannel(value: unknown): SearchChannel | undefined {
  const label = asText(value)?.toLowerCase()
  if (label === 'self' || label === 'local' || label === 'index') return 'self'
  if (label === 'flyb') return 'flyb'
  return undefined
}

function siteName(doc: Untrusted<PredictDocWire>, details: Untrusted<PredictDocDetailsWire>, pageUrl: string | null): string | null {
  const site = untrusted<{ name: string; Name: string; host: string; Host: string }>(details.site)
  return asText(doc.site_name) ?? asText(details.site)
    ?? asText(site?.name) ?? asText(site?.Name) ?? asText(site?.host) ?? asText(site?.Host)
    ?? (pageUrl ? new URL(pageUrl).hostname : null)
}

function toPredictImage(doc: Untrusted<PredictDocWire>, index: number): SearchImage {
  const details = untrusted<PredictDocDetailsWire>(doc.details) ?? {}
  const images = untrustedList<PredictDocImageWire>(details.images)
  const primary = images.find(image => safeHttpUrl(image.image_url)) ?? images[0] ?? {}
  const imageUrl = safeHttpUrl(doc.image_url) ?? safeHttpUrl(primary.image_url)
  const thumbnails = rankedThumbnails(doc, images)
  const pageUrl = safeHttpUrl(details.page_url) ?? safeHttpUrl(doc.page_url)
  const source = documentChannel(doc.source)
  return {
    id: asId(doc.id) ?? asId(doc.point_id) ?? asId(doc.image_id) ?? `image-${index + 1}`,
    title: asText(doc.title) ?? asText(primary.alt) ?? `未命名图片 ${index + 1}`,
    imageUrl,
    thumbnailUrl: uniqueHttpUrls(thumbnails)[0] ?? null,
    alternateUrls: uniqueHttpUrls([doc.image_url, ...thumbnails]).filter(url => url !== imageUrl),
    similarity: parseSimilarity(doc.similarity) ?? parseSimilarity(doc.similiar),
    categoryId: null,
    category: asText(doc.category) ?? asText(details.category),
    scenes: [],
    sceneLabel: null,
    labelStatus: null,
    description: '',
    foodState: null,
    heatingGroup: null,
    width: asPositiveNumber(doc.width) ?? asPositiveNumber(primary.width),
    height: asPositiveNumber(doc.height) ?? asPositiveNumber(primary.height),
    ...(source ? { source } : {}),
    pageUrl,
    siteName: siteName(doc, details, pageUrl),
    snippets: Array.isArray(details.text_snippets) ? asTextList(details.text_snippets) : asTextList(doc.text_snippets),
  }
}

/** Predict body → SearchResponse. `result` must be a document list or an envelope with a `results` list. */
export function fromPredictResponse(payload: unknown): SearchResponse {
  const body = untrusted<PredictResponseWire>(payload)
  const result = body?.result
  const envelope = untrusted<PredictUpstreamEnvelopeWire>(result) ?? {}
  const rows = Array.isArray(result) ? result : Array.isArray(envelope.results) ? envelope.results : null
  if (!rows) throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。')
  const status = asText(envelope.status)
  const message = status === 'upstream_empty_response'
    ? '上游搜索服务暂未返回结果，请稍后重试或更换图片。'
    : asText(untrusted<{ message: string }>(envelope.upstream_error)?.message) ?? asText(envelope.message)
  return {
    items: untrustedList<PredictDocWire>(rows).map(toPredictImage),
    traceId: asText(body?.trace_id) ?? asText(envelope.request_id) ?? asText(envelope.trace_id),
    ...(message ? { message } : {}),
  }
}
