import { ApiError, requestJson, requestJsonResult, type ApiLabels, type ImageUpdateRequestWire } from '../../shared/api'
import { asObject, asText } from '../../shared/lib/parse'
import { DEFAULT_SCENE, toApiSceneCode } from '../scene'
import {
  toImageDetail,
  toVectorState,
  toVectorSync,
  type ImageDetail,
  type ImageVectorState,
  type VectorSyncOutcome,
} from './detail'
import {
  toIndexResponse,
  toListImagesResponse,
  type IndexResponse,
  type LabelStatus,
  type ListImagesResponse,
} from './model'

const LIST_LABELS: ApiLabels = { service: '图库服务', endpoint: '图库接口', action: '图库列表' }
const INDEX_LABELS: ApiLabels = { service: '入库服务', endpoint: '入库接口', action: '入库' }
const IMAGE_LABELS: ApiLabels = { service: '图库服务', endpoint: '图片接口', action: '图片操作' }

// -- GET /api/images ----------------------------------------------------------------------

export interface ListImagesOptions {
  page?: number
  pageSize?: number
  q?: string | null
  /** Legacy: one category by code, name or id (no subtree). */
  category?: string | null
  /** The category and every category below it. */
  categoryId?: number | null
  labelStatus?: LabelStatus | null
  /** Only images whose vector store side needs attention (stale payload or no vector), by the server's ledger. */
  outOfSync?: boolean
  /** One tag, matched exactly (case-sensitive) against the image's tags; blank: no filter. */
  tag?: string | null
  /** Default general (PG v0.1). Do not omit to mean "all scenes". */
  scene?: string
  baseUrl?: string
  signal?: AbortSignal
  traceId?: string | null
}

/** Build query: omit empty q / tag / 「全部类别」; page_size defaults to 24; scene defaults to general. */
export function buildListImagesQuery({
  page = 1,
  pageSize = 24,
  q,
  category,
  categoryId,
  labelStatus,
  outOfSync,
  tag,
  scene = DEFAULT_SCENE,
  traceId,
}: Pick<ListImagesOptions, 'page' | 'pageSize' | 'q' | 'category' | 'categoryId' | 'labelStatus' | 'outOfSync' | 'tag' | 'scene' | 'traceId'>): URLSearchParams {
  const params = new URLSearchParams()
  params.set('page', String(Math.max(1, Math.floor(page) || 1)))
  params.set('page_size', String(pageSize > 0 ? Math.floor(pageSize) : 24))
  params.set('scene', toApiSceneCode(scene, DEFAULT_SCENE))
  const trimmedQ = typeof q === 'string' ? q.trim() : ''
  if (trimmedQ) params.set('q', trimmedQ)
  const trimmedCategory = typeof category === 'string' ? category.trim() : ''
  if (trimmedCategory && trimmedCategory !== '全部类别') params.set('category', trimmedCategory)
  if (typeof categoryId === 'number' && Number.isSafeInteger(categoryId) && categoryId > 0) params.set('category_id', String(categoryId))
  if (labelStatus) params.set('label_status', labelStatus)
  if (outOfSync) params.set('out_of_sync', 'true')
  const trimmedTag = typeof tag === 'string' ? tag.trim() : ''
  if (trimmedTag) params.set('tag', trimmedTag)
  if (traceId?.trim()) params.set('trace_id', traceId.trim())
  return params
}

/** List library images via GET /api/images. */
export async function listImages({ baseUrl, signal, ...query }: ListImagesOptions = {}): Promise<ListImagesResponse> {
  const payload = await requestJson('/api/images', {
    baseUrl,
    query: buildListImagesQuery(query),
    signal,
    labels: LIST_LABELS,
  })
  return toListImagesResponse(payload)
}

// -- POST /api/index ----------------------------------------------------------------------

export const MAX_INDEX_FILES = 100

/** The optional food annotation of an index request (image_foods); omitted fields stay empty. */
export interface IndexFoodFields {
  foodState?: string | null
  foodProcessing?: string | null
  foodCount?: number | null
  totalMassG?: number | null
  waterMassG?: number | null
  mainIngredientMassG?: number | null
}

const FOOD_FORM_KEYS: ReadonlyArray<[keyof IndexFoodFields, string]> = [
  ['foodState', 'food_state'],
  ['foodProcessing', 'food_processing'],
  ['foodCount', 'food_count'],
  ['totalMassG', 'total_mass_g'],
  ['waterMassG', 'water_mass_g'],
  ['mainIngredientMassG', 'main_ingredient_mass_g'],
]

export interface IndexOptions extends IndexFoodFields {
  file?: File
  files?: File[]
  /** Category code (categories.code, e.g. dish.steak); the server resolves it. */
  category?: string
  tags?: string[]
  /** Scene hints: only checked by the server, membership follows the scene rules. */
  scenes?: string[]
  /** 图片描述 (P6); omitted when blank, so the server default "" applies. */
  description?: string
  /** auto / verified; only meaningful with a category. Omitted: auto when there is one. */
  labelStatus?: 'auto' | 'verified'
  /** Editable enums (GET /api/enums); omitted: user_upload / unknown. */
  sourceType?: string
  license?: string
  baseUrl?: string
  signal?: AbortSignal
}

/**
 * Index via POST /api/index — multipart `file` (one) or `files` (≤100) plus the shared
 * category / tags / scenes / description / labelling / food fields. Throws when nothing was stored.
 */
export async function indexImage({
  file,
  files,
  category,
  tags,
  scenes,
  description,
  labelStatus,
  sourceType,
  license,
  baseUrl,
  signal,
  ...food
}: IndexOptions): Promise<IndexResponse> {
  const batch = files?.length ? files : file ? [file] : []
  if (!batch.length) throw new Error('请选择至少一张待入库图片。')
  if (batch.length > MAX_INDEX_FILES) throw new Error(`单次最多入库 ${MAX_INDEX_FILES} 张图片。`)
  for (const item of batch) {
    if (!item.size) throw new Error(`${item.name}：图片文件为空，请重新选择。`)
  }

  const form = new FormData()
  if (batch.length === 1) form.append('file', batch[0])
  else batch.forEach(item => form.append('files', item))
  if (category && category !== '未分类') form.append('category', category)
  if (category && category !== '未分类' && labelStatus) form.append('label_status', labelStatus)
  if (tags?.length) form.append('tags', JSON.stringify(tags))
  if (scenes?.length) form.append('scenes', JSON.stringify(scenes))
  if (description?.trim()) form.append('description', description)
  if (sourceType?.trim()) form.append('source_type', sourceType.trim())
  if (license?.trim()) form.append('license', license.trim())
  for (const [key, wire] of FOOD_FORM_KEYS) {
    const value = food[key]
    if (value !== null && value !== undefined && String(value).trim()) form.append(wire, String(value).trim())
  }

  const result = toIndexResponse(await requestJson('/api/index', {
    baseUrl,
    method: 'POST',
    form,
    signal,
    labels: INDEX_LABELS,
  }))
  // 200 with nothing indexed: report the first per-file failure.
  if (!result.indexed.length && result.failed.length) {
    const first = result.failed[0]
    const code = first.error ? `（${first.error}）` : ''
    throw new Error(first.detail ? `入库失败${code}：${first.detail}` : `入库失败${code}，请重试。`)
  }
  if (!result.indexed.length) {
    throw new Error('入库接口未返回已索引条目，请检查后端响应。')
  }
  return result
}

// -- one stored image: GET / PATCH / DELETE /api/images/{id}, POST …/vector-sync ---------

export interface ImageRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

export interface ImageDetailResult {
  image: ImageDetail
  vector: ImageVectorState
  traceId: string | null
}

/** A write the server accepted; vectorSync says how it reached the search index. */
export interface ImageUpdateResult {
  image: ImageDetail
  /** The vector state after the push; null when the server does not send it (read the detail then). */
  vector: ImageVectorState | null
  vectorSync: VectorSyncOutcome | null
  syncRequired: boolean
  traceId: string | null
}

export interface ImageVectorPushResult {
  imageId: string
  payloadVersion: number | null
  /** The vector state after the push; null when the server does not send it (read the detail then). */
  vector: ImageVectorState | null
  vectorSync: VectorSyncOutcome | null
  syncRequired: boolean
  traceId: string | null
}

/** The optional `vector` of a write body: parsed when present, null when absent. */
function vectorAfterWrite(value: unknown): ImageVectorState | null {
  return value === undefined || value === null ? null : toVectorState(value)
}

export interface ImageDeleteResult {
  imageId: string
  deletedAt: string | null
  /** done: the point and the files are gone too; queued: the deletion worker finishes them. */
  cleanup: 'done' | 'queued'
  traceId: string | null
}

/** The fields to change; null clears one (label_status never). Only what changed is sent. */
export type ImagePatch = ImageUpdateRequestWire

function imagePath(id: string, suffix = ''): string {
  return `/api/images/${encodeURIComponent(id)}${suffix}`
}

/** A 2xx whose body is not the contract's: the write may have happened, so the caller reads again. */
function unreadable(status: number, traceId: string | null): ApiError {
  return new ApiError('图片接口返回的数据格式不正确，请刷新后核对。', { status, traceId, code: 'IMAGE_PROTOCOL_ERROR' })
}

/** GET /api/images/{id}: every field of the image and its state in the current collection. */
export async function getImage(id: string, { baseUrl, signal }: ImageRequestOptions = {}): Promise<ImageDetailResult> {
  const result = await requestJsonResult(imagePath(id), { baseUrl, signal, labels: IMAGE_LABELS })
  const body = asObject(result.body)
  const image = toImageDetail(body?.image)
  if (body?.ok !== true || !image) throw unreadable(result.status, result.traceId)
  return { image, vector: toVectorState(body.vector), traceId: result.traceId }
}

/** PATCH /api/images/{id} with only the fields to change. Does not retry. */
export async function updateImage(id: string, patch: ImagePatch, { baseUrl, signal }: ImageRequestOptions = {}): Promise<ImageUpdateResult> {
  const result = await requestJsonResult(imagePath(id), { baseUrl, signal, method: 'PATCH', json: patch, labels: IMAGE_LABELS })
  const body = asObject(result.body)
  const image = toImageDetail(body?.image)
  if (body?.ok !== true || !image) throw unreadable(result.status, result.traceId)
  return {
    image,
    vector: vectorAfterWrite(body.vector),
    vectorSync: toVectorSync(body.vector_sync),
    syncRequired: body.sync_required !== false,
    traceId: result.traceId,
  }
}

/** POST /api/images/{id}/vector-sync: write the current payload to the current collection again. */
export async function pushImageVector(id: string, { baseUrl, signal }: ImageRequestOptions = {}): Promise<ImageVectorPushResult> {
  const result = await requestJsonResult(imagePath(id, '/vector-sync'), { baseUrl, signal, method: 'POST', labels: IMAGE_LABELS })
  const body = asObject(result.body)
  if (body?.ok !== true) throw unreadable(result.status, result.traceId)
  return {
    imageId: asText(body.image_id) ?? id,
    payloadVersion: typeof body.payload_version === 'number' ? body.payload_version : null,
    vector: vectorAfterWrite(body.vector),
    vectorSync: toVectorSync(body.vector_sync),
    syncRequired: body.sync_required !== false,
    traceId: result.traceId,
  }
}

export interface ImageReembedResult {
  imageId: string
  payloadVersion: number | null
  /** The vector state after the point was written. */
  vector: ImageVectorState
  traceId: string | null
}

/**
 * POST /api/images/{id}/reembed: embed the stored original again with the current model and
 * write the point (repairs a missing vector; the annotation does not change). Fails with
 * IMAGE_FILE_UNAVAILABLE when the original cannot be read back, NOT_READY when the encoder,
 * the vector store or the object storage is down.
 */
export async function reembedImage(id: string, { baseUrl, signal }: ImageRequestOptions = {}): Promise<ImageReembedResult> {
  const result = await requestJsonResult(imagePath(id, '/reembed'), { baseUrl, signal, method: 'POST', labels: IMAGE_LABELS })
  const body = asObject(result.body)
  if (body?.ok !== true) throw unreadable(result.status, result.traceId)
  return {
    imageId: asText(body.image_id) ?? id,
    payloadVersion: typeof body.payload_version === 'number' ? body.payload_version : null,
    vector: toVectorState(body.vector),
    traceId: result.traceId,
  }
}

/** DELETE /api/images/{id}. Deleting an image still queued for cleanup answers the same. */
export async function deleteImage(id: string, { baseUrl, signal }: ImageRequestOptions = {}): Promise<ImageDeleteResult> {
  const result = await requestJsonResult(imagePath(id), { baseUrl, signal, method: 'DELETE', labels: IMAGE_LABELS })
  const body = asObject(result.body)
  if (body?.ok !== true) throw unreadable(result.status, result.traceId)
  return {
    imageId: asText(body.image_id) ?? id,
    deletedAt: asText(body.deleted_at),
    cleanup: body.cleanup === 'done' ? 'done' : 'queued',
    traceId: result.traceId,
  }
}
