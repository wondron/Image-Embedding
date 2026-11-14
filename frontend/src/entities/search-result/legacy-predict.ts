/**
 * 现网 Global search — POST /api/search/global/* — FlyB results through FoodService:
 * `{ ok, trace_id, result }`, where `result` is a list of result cards (the same card as a
 * self-built hit, §6.1.1) or the upstream-empty status (`{ results, status, upstream_error, request_id }`).
 * The card fields are read by the shared readCardFields; this file reads the FlyB `details`
 * (thumbnail size maps, source page, site).
 */
import {
  asObject,
  asPositiveNumber,
  asText,
  untrusted,
  untrustedList,
  type JsonObject,
  type Untrusted,
} from '../../shared/lib/parse'
import { safeHttpUrl, uniqueHttpUrls } from '../../shared/lib/safeHttpUrl'
import type {
  PredictHitDetailsWire,
  PredictHitWire,
  PredictResponseWire,
  PredictUpstreamEmptyWire,
  SearchHitImageWire,
} from '../../shared/api'
import { readCardFields } from './mapper'
import type { SearchImage, SearchResponse } from './model'

const THUMBNAIL_SIZES = ['large', 'medium', 'small']

/** A size map with lower-case keys (FlyB sends Large / Medium / Small / Tiny), or {} for a plain string / anything else. */
function sizeMap(value: unknown): JsonObject {
  const map = asObject(value)
  return map ? Object.fromEntries(Object.entries(map).map(([size, url]) => [size.toLowerCase(), url])) : {}
}

/**
 * Thumbnail candidates across details.images[], ranked by size first: every `large`, then every
 * `medium`, then `small`, then other keys (such as `tiny`); size names in any case. A plain-string
 * thumbnail counts as large. Entries are unchecked; callers filter them.
 */
function rankedThumbnails(images: Untrusted<SearchHitImageWire>[]): unknown[] {
  const collections = images.map(image => image.thumbnail_url)
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

/** FlyB HostInfo: `{ Hostname, IconUrl }` as sent today (Hostname is the site's name), or older shapes; else the page's host. */
function siteName(details: Untrusted<PredictHitDetailsWire>, pageUrl: string | null): string | null {
  const site = untrusted<{ Hostname: string; name: string; Name: string; host: string; Host: string }>(details.site)
  return asText(details.site)
    ?? asText(site?.Hostname) ?? asText(site?.name) ?? asText(site?.Name) ?? asText(site?.host) ?? asText(site?.Host)
    ?? (pageUrl ? new URL(pageUrl).hostname : null)
}

function toPredictImage(doc: Untrusted<PredictHitWire>, index: number): SearchImage {
  const card = readCardFields(doc)
  const details = untrusted<PredictHitDetailsWire>(doc.details) ?? {}
  const images = untrustedList<SearchHitImageWire>(details.images)
  const primary = images.find(image => safeHttpUrl(image.image_url)) ?? images[0] ?? {}
  // original_url is the first image's original as FlyB sent it; when that one is not a safe
  // HTTP(S) link, the first image that has one
  const imageUrl = card.imageUrl ?? safeHttpUrl(primary.image_url)
  const thumbnails = uniqueHttpUrls(rankedThumbnails(images))
  const pageUrl = safeHttpUrl(details.page_url)
  return {
    ...card,
    id: `image-${index + 1}`,
    title: card.tags || asText(primary.alt) || `未命名图片 ${index + 1}`,
    imageUrl,
    thumbnailUrl: thumbnails[0] ?? null,
    alternateUrls: thumbnails.filter(url => url !== imageUrl),
    categoryId: null,
    category: null,
    scenes: [],
    sceneLabel: null,
    labelStatus: null,
    foodState: null,
    heatingGroup: null,
    width: asPositiveNumber(primary.width),
    height: asPositiveNumber(primary.height),
    pageUrl,
    siteName: siteName(details, pageUrl),
  }
}

/** Predict body → SearchResponse. `result` must be a card list or the upstream-empty status with a `results` list. */
export function fromPredictResponse(payload: unknown): SearchResponse {
  const body = untrusted<PredictResponseWire>(payload)
  const result = body?.result
  const envelope = untrusted<PredictUpstreamEmptyWire>(result) ?? {}
  const rows = Array.isArray(result) ? result : Array.isArray(envelope.results) ? envelope.results : null
  if (!rows) throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。')
  const status = asText(envelope.status)
  const message = status === 'upstream_empty_response'
    ? '上游搜索服务暂未返回结果，请稍后重试或更换图片。'
    : asText(untrusted<{ message: string }>(envelope.upstream_error)?.message)
  return {
    items: untrustedList<PredictHitWire>(rows).map(toPredictImage),
    traceId: asText(body?.trace_id) ?? asText(envelope.request_id),
    ...(message ? { message } : {}),
  }
}
