import { asText } from '../../shared/lib/parse'
import { uniqueHttpUrls } from '../../shared/lib/safeHttpUrl'
import type { FoodState, HeatingGroup, LabelStatus } from '../image'

/**
 * Search channel for badges. Self-built search reports only self | empty (§6.1.1); 业务搜图
 * (POST /api/search/auto/*, §6.6) also 'global', for results of its 现网 Global fallback.
 * 'flyb' is kept for the legacy UI branches (contract W5) and is not produced for new results.
 */
export type SearchChannel = 'self' | 'empty' | 'flyb' | 'global'

/** 业务搜图: what happened to the Global fallback (envelope `fallback.status`). */
export type SearchFallbackStatus = 'not_needed' | 'disabled' | 'hit' | 'empty' | 'failed'

export interface SearchFallback {
  status: SearchFallbackStatus
  /** status=failed: the error code (e.g. UPSTREAM_TIMEOUT) and what it means; null otherwise. */
  error: string | null
  detail: string | null
}

/**
 * One result card, from either channel: a self-built hit (POST /api/search) or a 现网 FlyB result
 * (POST /api/search/global/*). Both send the same card (§6.1.1): `tags` / `description` /
 * `original_url` / `similarity` at the top level, everything else in `details.*`.
 */
export interface SearchImage {
  /** 自建 ← `details.id`; FlyB results have none: 「image-N」. */
  id: string
  /** Display name. 自建: scene_label → category → tags → 「未命名图片 N」; FlyB: tags (its title) → image alt → 「未命名图片 N」. */
  title: string
  /** ← `tags`: 自建 the image's tags joined with「、」, FlyB the result title; "" when none. */
  tags: string
  /** Full image ← `original_url`. */
  imageUrl: string | null
  /** Thumbnail ← `details.images[]` `thumbnail_url` (自建: the 512 px WebP; FlyB: the largest size). */
  thumbnailUrl: string | null
  /** Further candidates tried after imageUrl (FlyB thumbnail sizes, ranked); [] for self-built hits. */
  alternateUrls: string[]
  /** ← `similarity`: finite score in [-1, 1]; null when not returned (never 0 by default). */
  similarity: number | null
  categoryId: number | null
  /** Category display name ← `details.category`. */
  category: string | null
  scenes: string[]
  /** Label of the searched scene ← `details.scene_label` (string | null, P8). */
  sceneLabel: string | null
  labelStatus: LabelStatus | null
  /** ← `description`: 自建 images.description, FlyB the result's text snippets; "" when none. */
  description: string
  /** Sent only for scenes with outputs_heating; null otherwise. */
  foodState: FoodState | null
  /** Sent only for scenes with outputs_heating; null otherwise or when the category has none. */
  heatingGroup: HeatingGroup | null
  width: number | null
  height: number | null
  /** Envelope `outputs_heating`, copied onto each card (P9: never inferred). */
  outputsHeating?: boolean
  /** Envelope channel, copied onto each card; absent when the API did not say. */
  source?: SearchChannel
  /** FlyB only: source page / site (`details.page_url` / `details.site`); null for self-built hits. */
  pageUrl: string | null
  siteName: string | null
}

export interface SearchResponse {
  items: SearchImage[]
  traceId: string | null
  /** Text for people, e.g. why the result is empty. */
  message?: string
  /** Envelope channel: self | empty (自建), self | global | empty (业务搜图); absent for 现网 Global. */
  source?: SearchChannel
  /** Scene actually searched (envelope `scene`). */
  scene?: string
  /** Envelope `outputs_heating` only — never inferred from the scene seed (P9). */
  outputsHeating?: boolean
  /** 业务搜图 only: what happened to the Global fallback. */
  fallback?: SearchFallback
}

/** Prefer scene_label when present; else generic category. */
export function displayCategoryLabel(item: {
  sceneLabel?: string | null
  category?: string | null
}): string | null {
  return asText(item.sceneLabel) ?? asText(item.category) ?? null
}

type ImageUrls = Pick<SearchImage, 'imageUrl' | 'thumbnailUrl'> & { alternateUrls?: readonly string[] }

/**
 * URLs to try, in order, where the full image is wanted (the detail view, downloads): original,
 * alternates, thumbnail. Only safe HTTP(S) URLs, without duplicates. Availability is checked when
 * an image is loaded.
 */
export function getImageSources(item: ImageUrls): string[] {
  return uniqueHttpUrls([item.imageUrl, ...(item.alternateUrls ?? []), item.thumbnailUrl])
}

/**
 * URLs to try, in order, where results are listed (grid, table): the thumbnail (for a self-built
 * hit the 512 px WebP), the other thumbnail sizes, and the original only when none of them loads.
 */
export function getThumbnailSources(item: ImageUrls): string[] {
  return uniqueHttpUrls([item.thumbnailUrl, ...(item.alternateUrls ?? []), item.imageUrl])
}
