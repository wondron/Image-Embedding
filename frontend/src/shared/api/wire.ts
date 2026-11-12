/**
 * Backend wire types (snake_case) — the single place the frontend states the HTTP contract.
 *
 * Mirrors backend/app/schemas/{retrieval,common,predict}.py and
 * docs/image-search-pg-v01-data-contract.md §6.1. Update both sides together.
 * Responses are still read defensively (see shared/lib/parse `Untrusted`): these types say what
 * the backend promises, not what a proxy or an older server may actually send.
 */

// -- shared parts ------------------------------------------------------------------------

/**
 * Editable enum (config/param/enums.yaml): GET /api/enums/food_state lists the values and labels.
 * It starts with raw / cooked / frozen and can gain values at runtime (POST /api/enums/food_state/values).
 */
export type FoodStateWire = string
/** System enum (app.core.enums.LabelStatus): the values are fixed in code. */
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

// -- /api/enums (schemas/enums.py) ------------------------------------------------------

/** Success bodies do not include trace_id; read X-Trace-Id from the response header. */

export interface EnumValueWire {
  value: string
  label: string
  description?: string
}

export interface EnumItemWire {
  name: string
  label: string
  description?: string
  editable: boolean
  values: EnumValueWire[]
}

/** GET /api/enums */
export interface EnumListResponseWire {
  ok: true
  enums: EnumItemWire[]
  /** Present when the file is broken and the body is the last good cache. */
  file_error?: string | null
}

/** GET /api/enums/{name} and POST /api/enums/{name}/values (201). No file_error field. */
export interface EnumResponseWire {
  ok: true
  enum: EnumItemWire
}

/** POST /api/enums/{name}/values — only these three fields. */
export interface AddEnumValueRequestWire {
  value: string
  label: string
  description: string
}

// -- /api/categories (schemas/categories.py) --------------------------------------------

/** Success bodies do not include trace_id; read X-Trace-Id from the response header. */

/** System enum (app.core.enums.NameKind). */
export type CategoryNameKindWire = 'primary' | 'alias'

/** One row of category_names. */
export interface CategoryNameWire {
  /** Lowercase language code: zh, en, … */
  lang: string
  name: string
  kind: CategoryNameKindWire
}

/** schemas/categories.py CategoryItem. */
export interface CategoryItemWire {
  id: number
  code: string
  /** null for a root. */
  parent_id: number | null
  /** Id chain from the root, e.g. 2.15.137; maintained by the database. */
  path: string
  /** The zh primary name (the `category` of search hits); null when the node has none. */
  name: string | null
  names: CategoryNameWire[]
  /** The node's own cuisines; empty means it uses its nearest ancestor's. */
  cuisines: string[]
  /** The node's own heating group; null means it uses its nearest ancestor's. */
  heating_group: HeatingGroupWire | null
  sort_order: number
  is_active: boolean
  /** Images labeled with exactly this category. */
  image_count: number
  /** Scene rules that point at this category. */
  scene_rule_count: number
  created_at: string | null
  updated_at: string | null
}

/** GET /api/categories: the whole tree, parents before children, siblings in display order. */
export interface CategoryListResponseWire {
  ok: true
  categories: CategoryItemWire[]
  heating_groups: HeatingGroupWire[]
  /** The codes a root may use (the four domains). */
  root_codes: string[]
}

/** POST /api/categories (201) and PATCH /api/categories/{id}. */
export interface CategoryResponseWire {
  ok: true
  category: CategoryItemWire
}

/** schemas/categories.py CategorySceneSync: a move or a cuisines change, for the stored images below the node. */
export interface CategorySceneSyncWire {
  /** Images labelled with the node or one below it; their scenes were recomputed in the same transaction. */
  images: number
  /** Of them, whose effective scenes changed. */
  changed: number
  /** Of the changed, whose new payload is in the current vector collection. */
  pushed: number
  /** Of the changed, not yet there (the reconciler or POST /api/scenes/sync catches up). */
  pending: number
  vector_sync: 'pushed' | 'partial' | 'not_ready' | 'failed'
  sync_required: boolean
}

/** PATCH /api/categories/{id}. */
export interface CategoryUpdateResponseWire extends CategoryResponseWire {
  /** Only after a move or a cuisines change; null for any other patch. */
  scene_sync?: CategorySceneSyncWire | null
}

/** DELETE /api/categories/{id}. */
export interface CategoryDeleteResponseWire {
  ok: true
  deleted: { id: number; code: string }
}

/** POST /api/categories. */
export interface CategoryCreateRequestWire {
  code: string
  parent_id: number | null
  /** Must include the zh primary name. */
  names: CategoryNameWire[]
  cuisines: string[]
  heating_group_id: number | null
  sort_order: number
  is_active: boolean
}

/**
 * PATCH /api/categories/{id}: only the fields to change. parent_id null moves the node to the
 * root, heating_group_id null clears it; names replaces every name.
 */
export type CategoryUpdateRequestWire = Partial<CategoryCreateRequestWire>

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
  tags: string[]
  width: number | null
  height: number | null
  /** Present only when the scene has outputs_heating. */
  food_state?: FoodStateWire | null
  /** Present only when the scene has outputs_heating; null when the category has no heating group. */
  heating_group?: HeatingGroupWire | null
  created_at: string | null
  updated_at: string | null
  /** The image in the current collection (as the detail's vector.status); absent from older servers. */
  vector_status?: ImageVectorStateWire['status']
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
  tags: string[]
  width: number
  height: number
  payload_version: number | null
  created_at: string | null
  /** false: the same bytes were stored before and their labels were kept (absent on older servers). */
  created?: boolean | null
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

// -- one stored image: GET / PATCH / DELETE /api/images/{id}, POST …/vector-sync ----------

/** schemas/images.py ImageEmbeddingRecord. */
export interface ImageLedgerEntryWire {
  model_id: number
  name: string
  version: string
  collection: string
  model_status: string
  synced_version: number
  embedded_at: string | null
}

/** schemas/images.py ImageDetail: the list item's fields plus what only the detail shows. */
export interface ImageDetailWire {
  id: string
  sha256: string
  original_url: string
  thumb_url: string
  width: number
  height: number
  category_id: number | null
  category: string | null
  category_code: string | null
  label_status: LabelStatusWire
  scenes: string[]
  description: string
  tags: string[]
  source_type: string
  license: string
  food_state: FoodStateWire | null
  food_processing: string | null
  food_count: number | null
  total_mass_g: number | null
  water_mass_g: number | null
  main_ingredient_mass_g: number | null
  heating_group: HeatingGroupWire | null
  payload_version: number
  created_at: string | null
  updated_at: string | null
  embeddings: ImageLedgerEntryWire[]
}

/** schemas/images.py ImageVectorState. */
export interface ImageVectorStateWire {
  status: 'synced' | 'stale' | 'missing' | 'unavailable'
  collection?: string | null
  embedding_space_id?: string | null
  exists?: boolean | null
  payload?: Record<string, unknown> | null
  expected_payload?: Record<string, unknown> | null
  payload_matches?: boolean | null
  payload_version?: number | null
  synced_version?: number | null
  embedded_at?: string | null
  detail?: string | null
}

export type VectorSyncWire = 'pushed' | 'missing' | 'not_ready' | 'failed'

/** GET /api/images/{id}. */
export interface ImageDetailResponseWire {
  ok: true
  image: ImageDetailWire
  vector: ImageVectorStateWire
}

/** PATCH /api/images/{id}: only the fields to change; null clears (label_status never null). */
export interface ImageUpdateRequestWire {
  category_id?: number | null
  label_status?: LabelStatusWire
  description?: string | null
  tags?: string[] | null
  food_state?: string | null
  food_processing?: string | null
  food_count?: number | null
  total_mass_g?: number | null
  water_mass_g?: number | null
  main_ingredient_mass_g?: number | null
}

export interface ImageUpdateResponseWire {
  ok: true
  image: ImageDetailWire
  /** The vector state after the push (as GET's vector); absent from older servers. */
  vector?: ImageVectorStateWire | null
  vector_sync: VectorSyncWire
  sync_required: boolean
}

/** POST /api/images/{id}/vector-sync. */
export interface ImageVectorSyncResponseWire {
  ok: true
  image_id: string
  payload_version: number
  /** The vector state after the push (as GET's vector); absent from older servers. */
  vector?: ImageVectorStateWire | null
  vector_sync: VectorSyncWire
  sync_required: boolean
}

/** DELETE /api/images/{id}. */
export interface ImageDeleteResponseWire {
  ok: true
  image_id: string
  deleted_at: string | null
  cleanup: 'done' | 'queued'
}

// -- task center: /api/index-tasks, /api/image-deletions (schemas/tasks.py) ----------------

/** System enum (app.core.enums.IndexTaskStatus): an index is synchronous, so only the outcome. */
export type IndexTaskStatusWire = 'succeeded' | 'failed'

/** schemas/tasks.py IndexTaskImage: the record's image as it is now. */
export interface IndexTaskImageWire {
  id: string
  thumb_url: string | null
  category_id: number | null
  category: string | null
  label_status: LabelStatusWire | null
}

/** schemas/tasks.py IndexTaskItem: one file of an index request. */
export interface IndexTaskWire {
  id: number
  filename: string
  status: IndexTaskStatusWire
  /** Kept after the image is deleted; null when the file failed. */
  image_id: string | null
  /** false: the same bytes were stored before (their annotation was kept); null when failed. */
  new_image: boolean | null
  error_code: string | null
  error_detail: string | null
  duration_ms: number | null
  trace_id: string | null
  created_at: string | null
  /** null when the file failed, or its image was deleted since. */
  image: IndexTaskImageWire | null
}

/** GET /api/index-tasks?status=&page=&page_size= (newest first). */
export interface IndexTaskListResponseWire {
  ok: true
  status: IndexTaskStatusWire | null
  page: number
  page_size: number
  /** Records matching the filter. */
  total: number
  /** Every record by outcome, whatever the filter. */
  counts: Record<IndexTaskStatusWire, number>
  items: IndexTaskWire[]
}

/** DELETE /api/index-tasks?status= */
export interface IndexTaskClearResponseWire {
  ok: true
  status: IndexTaskStatusWire
  deleted: number
}

/** schemas/tasks.py ImageDeletionItem: deleted from the gallery, point and files not yet removed. */
export interface ImageDeletionWire {
  image_id: string
  original_url: string | null
  thumb_url: string | null
  deleted_at: string | null
}

/** GET /api/image-deletions?limit= (oldest first). */
export interface ImageDeletionListResponseWire {
  ok: true
  total: number
  items: ImageDeletionWire[]
  /** false: POST /api/image-deletions/process answers NOT_READY now. */
  cleanup_ready: boolean
}

export interface ImageDeletionFailureWire {
  image_id: string
  error: string
  detail: string
}

/** POST /api/image-deletions/process {limit?} */
export interface ImageDeletionProcessResponseWire {
  ok: true
  scanned: number
  processed: string[]
  failed: ImageDeletionFailureWire[]
  remaining: number
}

// -- GET /api/ping ------------------------------------------------------------------------

/** schemas/predict.py PingResponse. */
export interface PingResponseWire {
  ok: boolean
  ts: string
  timeout_s: number
  max_image_bytes: number
}
