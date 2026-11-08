/**
 * Backend wire types (snake_case) — the single place the frontend states the HTTP contract.
 *
 * Mirrors backend/app/schemas/{retrieval,common,predict}.py and
 * docs/image-search-pg-v01-data-contract.md §6.1. Update both sides together.
 * Responses are still read defensively (see shared/lib/parse `Untrusted`): these types say what
 * the backend promises, not what a proxy or an older server may actually send.
 */

// -- shared parts ------------------------------------------------------------------------

export type FoodStateWire = 'raw' | 'cooked' | 'frozen'
export type LabelStatusWire = 'unlabeled' | 'auto' | 'verified'

/** schemas/retrieval.py HeatingGroup. */
export interface HeatingGroupWire {
  id: number
  code: string
  name: string
}

/** schemas/common.py ErrorResponse — every failure, any endpoint. */
export interface ErrorResponseWire {
  ok: false
  /** Stable code, e.g. SEARCH_BAD_IMAGE. */
  error: string
  detail: string
  trace_id: string
  /** Field-level list, VALIDATION_ERROR only. */
  errors?: Array<Record<string, unknown>>
}

// -- POST /api/search (§6.1.1) ----------------------------------------------------------

/** schemas/retrieval.py SearchJsonRequest (JSON body; multipart sends the same names). */
export interface SearchJsonRequestWire {
  img_url: string
  scene?: string | null
  top_k?: number
  /** Omit/null = server default 0.55; 0 = reject off; else [0.30, 1]. */
  score_threshold?: number | null
  trace_id?: string | null
}

/** schemas/retrieval.py SearchHit. */
export interface SearchHitWire {
  id: string
  original_url: string | null
  thumb_url: string | null
  similarity: number | null
  category_id: number | null
  category: string | null
  scenes: string[] | null
  /** Present only when a scene label was mapped (exclude_unset). */
  scene_label?: string | null
  label_status: LabelStatusWire | null
  description: string
  width: number | null
  height: number | null
  /** Present only when the scene has outputs_heating. */
  food_state?: FoodStateWire | null
  /** Present only when the scene has outputs_heating; null when the category has no heating group. */
  heating_group?: HeatingGroupWire | null
}

/** schemas/retrieval.py SearchResponse. */
export interface SearchResponseWire {
  ok: true
  trace_id: string
  source: 'self' | 'empty'
  scene: string
  outputs_heating: boolean
  /** Always an array; [] when source=empty (P3/P4). */
  items: SearchHitWire[]
  message?: string | null
}

// -- GET /api/images (§6.1.2) -----------------------------------------------------------

/** schemas/retrieval.py ListImageItem. */
export interface ListImageItemWire {
  id: string
  original_url: string | null
  thumb_url: string | null
  category_id: number | null
  category: string | null
  scenes: string[] | null
  label_status: LabelStatusWire | null
  description: string
  width: number | null
  height: number | null
  /** Present only when the scene has outputs_heating. */
  food_state?: FoodStateWire | null
  /** Present only when the scene has outputs_heating; null when the category has no heating group. */
  heating_group?: HeatingGroupWire | null
  created_at: string | null
  updated_at: string | null
}

/** schemas/retrieval.py ListImagesResponse. */
export interface ListImagesResponseWire {
  ok: true
  trace_id: string
  scene: string
  page: number
  page_size: number
  total: number
  items: ListImageItemWire[]
}

// -- POST /api/index (§3.1) -------------------------------------------------------------

/** schemas/retrieval.py IndexedImage. */
export interface IndexedImageWire {
  id: string
  filename: string
  original_url: string
  thumb_url: string
  category_id: number | null
  scenes: string[]
  label_status: LabelStatusWire
  food_state: FoodStateWire | null
  description: string
  width: number
  height: number
  payload_version: number | null
  created_at: string | null
}

/** schemas/retrieval.py IndexFailure. */
export interface IndexFailureWire {
  filename: string
  /** Error code, e.g. INDEX_BAD_IMAGE. */
  error: string
  detail: string
}

/** schemas/retrieval.py IndexResponse. */
export interface IndexResponseWire {
  ok: true
  trace_id: string
  indexed: IndexedImageWire[]
  failed: IndexFailureWire[]
}

// -- POST /api/predict/searchImage_* (现网 Global · legacy, outside §6.1) ------------------

/** schemas/predict.py PredictUrlBody (searchImage_url JSON body). */
export interface PredictUrlBodyWire {
  img_url: string
  machine_info?: string | null
  trace_id?: string | null
  save_res?: boolean | null
}

/** schemas/predict.py PredictResponse: `result` is a FlyB document list or an upstream status envelope. */
export interface PredictResponseWire {
  ok: true
  trace_id: string
  result: PredictDocWire[] | PredictUpstreamEnvelopeWire
}

/** Upstream status envelope inside `result` (e.g. status=upstream_empty_response). */
export interface PredictUpstreamEnvelopeWire {
  results: PredictDocWire[]
  status?: string
  message?: string
  request_id?: string
  trace_id?: string
  upstream_error?: { code?: number; message?: string }
}

/** A thumbnail is one URL, or URLs keyed by size (large / medium / small / …). */
export type PredictThumbnailWire = string | Record<string, unknown>

/** FlyB search document (legacy shape; P7 forbids these fields on §6.1 hits). */
export interface PredictDocWire {
  id?: string | number
  point_id?: string | number
  image_id?: string | number
  title?: string
  image_url?: PredictThumbnailWire
  thumbnail_url?: PredictThumbnailWire
  similarity?: number
  /** Upstream misspelling, still sent by FlyB. */
  similiar?: number
  category?: string
  source?: string
  site_name?: string
  page_url?: string
  text_snippets?: string[]
  width?: number
  height?: number
  details?: PredictDocDetailsWire
}

export interface PredictDocDetailsWire {
  category?: string
  page_url?: string
  site?: string | { name?: string; Name?: string; host?: string; Host?: string }
  text_snippets?: string[]
  images?: PredictDocImageWire[]
}

export interface PredictDocImageWire {
  image_url?: string
  thumbnail_url?: PredictThumbnailWire
  alt?: string
  width?: number
  height?: number
}

// -- GET /api/ping ------------------------------------------------------------------------

/** schemas/predict.py PingResponse. */
export interface PingResponseWire {
  ok: boolean
  ts: string
  timeout_s: number
  max_image_bytes: number
}
