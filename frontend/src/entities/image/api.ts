import { requestJson, type ApiLabels } from '../../shared/api'
import { DEFAULT_SCENE, toApiSceneCode } from '../scene'
import {
  toIndexResponse,
  toListImagesResponse,
  type IndexResponse,
  type ListImagesResponse,
} from './model'

const LIST_LABELS: ApiLabels = { service: '图库服务', endpoint: '图库接口', action: '图库列表' }
const INDEX_LABELS: ApiLabels = { service: '入库服务', endpoint: '入库接口', action: '入库' }

// -- GET /api/images ----------------------------------------------------------------------

export interface ListImagesOptions {
  page?: number
  pageSize?: number
  q?: string | null
  category?: string | null
  /** Default general (PG v0.1). Do not omit to mean "all scenes". */
  scene?: string
  baseUrl?: string
  signal?: AbortSignal
  traceId?: string | null
}

/** Build query: omit empty q / 「全部类别」; page_size defaults to 24; scene defaults to general. */
export function buildListImagesQuery({
  page = 1,
  pageSize = 24,
  q,
  category,
  scene = DEFAULT_SCENE,
  traceId,
}: Pick<ListImagesOptions, 'page' | 'pageSize' | 'q' | 'category' | 'scene' | 'traceId'>): URLSearchParams {
  const params = new URLSearchParams()
  params.set('page', String(Math.max(1, Math.floor(page) || 1)))
  params.set('page_size', String(pageSize > 0 ? Math.floor(pageSize) : 24))
  params.set('scene', toApiSceneCode(scene, DEFAULT_SCENE))
  const trimmedQ = typeof q === 'string' ? q.trim() : ''
  if (trimmedQ) params.set('q', trimmedQ)
  const trimmedCategory = typeof category === 'string' ? category.trim() : ''
  if (trimmedCategory && trimmedCategory !== '全部类别') params.set('category', trimmedCategory)
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
