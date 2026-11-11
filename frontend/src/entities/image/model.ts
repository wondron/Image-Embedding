import {
  asId,
  asInteger,
  asPositiveNumber,
  asText,
  asTextList,
  untrusted,
  untrustedList,
  type Untrusted,
} from '../../shared/lib/parse'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import type {
  HeatingGroupWire,
  IndexFailureWire,
  IndexResponseWire,
  IndexedImageWire,
  ListImageItemWire,
  ListImagesResponseWire,
} from '../../shared/api'
import { DEFAULT_SCENE, normalizeSceneCode, toApiSceneCode } from '../scene'
import type { VectorStatus } from './detail'

// -- value types (shared by library, index and search) -----------------------------------

/**
 * A `food_state` value as stored. Editable enum: it is not limited to the initial raw / cooked / frozen;
 * the values and their labels come from GET /api/enums/food_state (see FOOD_STATE_ENUM).
 */
export type FoodState = string
export type LabelStatus = 'unlabeled' | 'auto' | 'verified'

/** Name of the food_state enum in GET /api/enums. */
export const FOOD_STATE_ENUM = 'food_state'

/** 加热组, kept as the wire object (§6.1.4: never flatten `name` into a string). */
export interface HeatingGroup {
  id: number
  code: string
  name: string
}

export interface SceneMembership {
  code: string
  /** rule | manual — unset until the API reports it. */
  origin?: 'rule' | 'manual'
}

/**
 * `food_state` → the value as sent (trimmed); null when absent, blank or not a string.
 * Values outside the initial three are kept: they were added to the editable enum at runtime.
 */
export function parseFoodState(value: unknown): FoodState | null {
  return asText(value)
}

/** `label_status` → LabelStatus; anything else → null. */
export function parseLabelStatus(value: unknown): LabelStatus | null {
  const status = asText(value)?.toLowerCase()
  return status === 'unlabeled' || status === 'auto' || status === 'verified' ? status : null
}

/** `heating_group` → HeatingGroup; null when absent, null, or missing id / code / name. */
export function parseHeatingGroup(value: unknown): HeatingGroup | null {
  const group = untrusted<HeatingGroupWire>(value)
  if (!group) return null
  const id = asInteger(group.id)
  const code = asText(group.code)
  const name = asText(group.name)
  return id !== null && code && name ? { id, code, name } : null
}

/** `description` is a string; default "" (P6). Kept as sent. */
export function parseDescription(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** `scenes` → normalized scene codes, without duplicates. */
export function parseSceneCodes(value: unknown): string[] {
  return [...new Set(asTextList(value).map(code => normalizeSceneCode(code)))]
}

// -- GET /api/images (§6.1.2) -----------------------------------------------------------

/** One library image (ListImageItem; field map §6.1.4). */
export interface LibraryImageItem {
  id: string
  /** Display thumbnail ← `thumb_url` (https only). */
  imageUrl: string | null
  /** Full image ← `original_url` (https only). */
  originalUrl: string | null
  categoryId: number | null
  /** Category display name ← `category`. */
  category: string | null
  scenes: string[]
  /** One entry per scene code; `origin` stays unset until the API reports it. */
  sceneMemberships: SceneMembership[]
  labelStatus: LabelStatus | null
  description: string
  tags: string[]
  /** Sent only for scenes with outputs_heating; null otherwise. */
  foodState: FoodState | null
  /** Sent only for scenes with outputs_heating; null otherwise or when the category has none. */
  heatingGroup: HeatingGroup | null
  width: number | null
  height: number | null
  createdAt: string | null
  updatedAt: string | null
  /** The image in the current vector collection; null when the server does not say (older servers). */
  vectorStatus: VectorStatus | null
}

const VECTOR_STATUSES: readonly VectorStatus[] = ['synced', 'stale', 'missing', 'unavailable']

export interface ListImagesResponse {
  traceId: string | null
  scene: string
  page: number
  pageSize: number
  total: number
  items: LibraryImageItem[]
}

/** One `items[]` entry → LibraryImageItem; null when it has no id. */
export function toLibraryImage(value: unknown): LibraryImageItem | null {
  const item = untrusted<ListImageItemWire>(value)
  const id = asId(item?.id)
  if (!item || !id) return null
  const scenes = parseSceneCodes(item.scenes)
  return {
    id,
    imageUrl: safeHttpUrl(item.thumb_url),
    originalUrl: safeHttpUrl(item.original_url),
    categoryId: asInteger(item.category_id),
    category: asText(item.category),
    scenes,
    sceneMemberships: scenes.map(code => ({ code })),
    labelStatus: parseLabelStatus(item.label_status),
    description: parseDescription(item.description),
    tags: asTextList(item.tags),
    foodState: parseFoodState(item.food_state),
    heatingGroup: parseHeatingGroup(item.heating_group),
    width: asPositiveNumber(item.width),
    height: asPositiveNumber(item.height),
    createdAt: asText(item.created_at),
    updatedAt: asText(item.updated_at),
    vectorStatus: VECTOR_STATUSES.includes(item.vector_status as VectorStatus) ? item.vector_status as VectorStatus : null,
  }
}

function intAtLeast(value: unknown, min: number, fallback: number): number {
  const number = asInteger(value)
  return number !== null && number >= min ? number : fallback
}

/** GET /api/images body → ListImagesResponse. `items` must be an array. */
export function toListImagesResponse(payload: unknown): ListImagesResponse {
  const body = untrusted<ListImagesResponseWire>(payload)
  if (!body || !Array.isArray(body.items)) {
    throw new Error('图库接口返回的数据格式不正确，请检查后端响应。')
  }
  return {
    traceId: asText(body.trace_id),
    scene: toApiSceneCode(body.scene, DEFAULT_SCENE),
    page: intAtLeast(body.page, 1, 1),
    pageSize: intAtLeast(body.page_size, 1, 24),
    total: intAtLeast(body.total, 0, 0),
    items: body.items.map(toLibraryImage).filter((item): item is LibraryImageItem => item !== null),
  }
}

// -- POST /api/index (§3.1) -------------------------------------------------------------

/** One stored image (IndexedImage); URL names follow LibraryImageItem. */
export interface IndexedImage {
  id: string
  filename: string | null
  /** Thumbnail ← `thumb_url`. */
  imageUrl: string | null
  /** Full image ← `original_url`. */
  originalUrl: string | null
  categoryId: number | null
  scenes: string[]
  labelStatus: LabelStatus | null
  foodState: FoodState | null
  description: string
  tags: string[]
  width: number | null
  height: number | null
  payloadVersion: number | null
  createdAt: string | null
  /**
   * true: stored by this request; false: the same bytes were already stored, and the first
   * upload's labels were kept (this request's were not written); null: the server did not say.
   */
  created: boolean | null
}

/** One file the batch could not store (IndexFailure). */
export interface IndexFailure {
  filename: string | null
  /** Error code, e.g. INDEX_BAD_IMAGE. */
  error: string | null
  detail: string | null
}

export interface IndexResponse {
  traceId: string | null
  indexed: IndexedImage[]
  failed: IndexFailure[]
}

function toIndexedImage(image: Untrusted<IndexedImageWire>): IndexedImage | null {
  const id = asId(image.id)
  if (!id) return null
  return {
    id,
    filename: asText(image.filename),
    imageUrl: safeHttpUrl(image.thumb_url),
    originalUrl: safeHttpUrl(image.original_url),
    categoryId: asInteger(image.category_id),
    scenes: parseSceneCodes(image.scenes),
    labelStatus: parseLabelStatus(image.label_status),
    foodState: parseFoodState(image.food_state),
    description: parseDescription(image.description),
    tags: asTextList(image.tags),
    width: asPositiveNumber(image.width),
    height: asPositiveNumber(image.height),
    payloadVersion: asInteger(image.payload_version),
    createdAt: asText(image.created_at),
    created: typeof image.created === 'boolean' ? image.created : null,
  }
}

/** POST /api/index body → IndexResponse. Missing lists read as empty. */
export function toIndexResponse(payload: unknown): IndexResponse {
  const body = untrusted<IndexResponseWire>(payload)
  return {
    traceId: asText(body?.trace_id),
    indexed: untrustedList<IndexedImageWire>(body?.indexed)
      .map(toIndexedImage)
      .filter((image): image is IndexedImage => image !== null),
    failed: untrustedList<IndexFailureWire>(body?.failed).map(failure => ({
      filename: asText(failure.filename),
      error: asText(failure.error),
      detail: asText(failure.detail),
    })),
  }
}
