import { asText } from '../../shared/lib/parse'
import { uniqueHttpUrls } from '../../shared/lib/safeHttpUrl'
import type { FoodState, HeatingGroup, LabelStatus } from '../image'

/**
 * Search channel for badges. Self-built search reports only self | empty (§6.1.1);
 * 'flyb' is kept for the legacy UI branches (contract W5) and is not produced for new results.
 */
export type SearchChannel = 'self' | 'empty' | 'flyb'

/** One result card: a §6.1 search hit (field map §6.1.4) or a legacy 现网 predict document. */
export interface SearchImage {
  id: string
  /** Display name: scene_label → category → 「未命名图片 N」 (the wire has no title, P5); predict keeps its document title. */
  title: string
  /** Full image ← `original_url`. */
  imageUrl: string | null
  /** Thumbnail ← `thumb_url`. */
  thumbnailUrl: string | null
  /** Further candidates tried after imageUrl (predict thumbnail sizes, ranked); [] for §6.1 hits. */
  alternateUrls: string[]
  /** Finite score in [-1, 1]; null when not returned (never 0 by default). */
  similarity: number | null
  categoryId: number | null
  /** Category display name ← `category`. */
  category: string | null
  scenes: string[]
  /** Label of the searched scene ← `scene_label` (string | null, P8). */
  sceneLabel: string | null
  labelStatus: LabelStatus | null
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
  /** Predict only: source page / site / text snippets. null / [] for §6.1 hits (P7). */
  pageUrl: string | null
  siteName: string | null
  snippets: string[]
}

export interface SearchResponse {
  items: SearchImage[]
  traceId: string | null
  /** Text for people, e.g. why the result is empty. */
  message?: string
  /** Envelope channel: self | empty; absent for predict. */
  source?: SearchChannel
  /** Scene actually searched (envelope `scene`). */
  scene?: string
  /** Envelope `outputs_heating` only — never inferred from the scene seed (P9). */
  outputsHeating?: boolean
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
