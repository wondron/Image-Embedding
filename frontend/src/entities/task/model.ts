/**
 * The task center (backend app/schemas/tasks.py): one record per indexed file, and the images
 * deleted from the gallery whose vector point and files still wait for their cleanup.
 */
import { asBoolean, asId, asInteger, asText, untrusted, untrustedList } from '../../shared/lib/parse'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import type {
  ImageDeletionFailureWire,
  ImageDeletionListResponseWire,
  ImageDeletionProcessResponseWire,
  ImageDeletionWire,
  IndexTaskImageWire,
  IndexTaskListResponseWire,
  IndexTaskWire,
} from '../../shared/api'
import { parseLabelStatus, type LabelStatus } from '../image'

export type IndexTaskStatus = 'succeeded' | 'failed'
export const INDEX_TASK_STATUSES: readonly IndexTaskStatus[] = ['succeeded', 'failed']
/** A succeeded record's image now: still stored, or deleted since (the record is kept). */
export type IndexTaskImageState = 'present' | 'deleted'
export const INDEX_TASK_IMAGE_STATES: readonly IndexTaskImageState[] = ['present', 'deleted']

/** The record's image as it is now. */
export interface IndexTaskImage {
  id: string
  /** Thumbnail ← `thumb_url` (https only). */
  imageUrl: string | null
  categoryId: number | null
  category: string | null
  labelStatus: LabelStatus | null
}

export interface IndexTask {
  id: number
  filename: string
  status: IndexTaskStatus
  imageId: string | null
  /** false: the same image was stored before and kept its annotation; null when the file failed. */
  newImage: boolean | null
  errorCode: string | null
  errorDetail: string | null
  durationMs: number | null
  traceId: string | null
  createdAt: string | null
  /** ← `image_state`: succeeded records only (null when failed); 'deleted' once the image is deleted. */
  imageState: IndexTaskImageState | null
  /** null when the file failed, or when its image has been deleted since. */
  image: IndexTaskImage | null
}

export interface IndexTaskCounts {
  succeeded: number
  failed: number
  /** Succeeded records whose image was deleted since (counted in `succeeded` too). */
  imageDeleted: number
}

export interface IndexTaskPage {
  /** This list GET's trace; never the original per-item index trace. */
  readTraceId?: string | null
  status: IndexTaskStatus | null
  /** The image-state filter of this page; null: not filtered. */
  imageState: IndexTaskImageState | null
  page: number
  pageSize: number
  /** Records matching the filter. */
  total: number
  /** Every record by outcome, whatever the filter. */
  counts: IndexTaskCounts
  items: IndexTask[]
}

export interface ImageDeletion {
  imageId: string
  /** The thumbnail is still stored until the cleanup runs. */
  imageUrl: string | null
  deletedAt: string | null
}

export interface ImageDeletionQueue {
  /** How many deletions wait in all (items holds at most the requested limit). */
  total: number
  items: ImageDeletion[]
  /** false: the retrieval stack is down and a cleanup answers NOT_READY now. */
  cleanupReady: boolean
}

export interface ImageDeletionFailure {
  imageId: string
  code: string
  detail: string
}

export interface ImageDeletionRun {
  scanned: number
  processed: string[]
  failed: ImageDeletionFailure[]
  remaining: number
}

export function parseIndexTaskStatus(value: unknown): IndexTaskStatus | null {
  return INDEX_TASK_STATUSES.includes(value as IndexTaskStatus) ? value as IndexTaskStatus : null
}

export function parseIndexTaskImageState(value: unknown): IndexTaskImageState | null {
  return INDEX_TASK_IMAGE_STATES.includes(value as IndexTaskImageState) ? value as IndexTaskImageState : null
}

function count(value: unknown): number {
  const number = asInteger(value)
  return number !== null && number >= 0 ? number : 0
}

function toTaskImage(value: unknown): IndexTaskImage | null {
  const image = untrusted<IndexTaskImageWire>(value)
  const id = asId(image?.id)
  if (!image || !id) return null
  return {
    id,
    imageUrl: safeHttpUrl(image.thumb_url),
    categoryId: asInteger(image.category_id),
    category: asText(image.category),
    labelStatus: parseLabelStatus(image.label_status),
  }
}

/** One `items[]` record; null without an id or a known status. */
export function toIndexTask(value: unknown): IndexTask | null {
  const task = untrusted<IndexTaskWire>(value)
  const id = asInteger(task?.id)
  const status = parseIndexTaskStatus(task?.status)
  if (!task || id === null || !status) return null
  const duration = asInteger(task.duration_ms)
  return {
    id,
    filename: asText(task.filename) ?? '未命名文件',
    status,
    imageId: asId(task.image_id),
    newImage: asBoolean(task.new_image) ?? null,
    errorCode: asText(task.error_code),
    errorDetail: asText(task.error_detail),
    durationMs: duration !== null && duration >= 0 ? duration : null,
    traceId: asText(task.trace_id),
    createdAt: asText(task.created_at),
    imageState: status === 'succeeded' ? parseIndexTaskImageState(task.image_state) : null,
    image: toTaskImage(task.image),
  }
}

/** GET /api/index-tasks body; null when it is not the contract's. */
export function toIndexTaskPage(payload: unknown): IndexTaskPage | null {
  const body = untrusted<IndexTaskListResponseWire>(payload)
  if (body?.ok !== true || !Array.isArray(body.items)) return null
  const counts = untrusted<IndexTaskListResponseWire['counts']>(body.counts)
  const page = asInteger(body.page)
  const pageSize = asInteger(body.page_size)
  return {
    status: parseIndexTaskStatus(body.status),
    imageState: parseIndexTaskImageState(body.image_state),
    page: page !== null && page >= 1 ? page : 1,
    pageSize: pageSize !== null && pageSize >= 1 ? pageSize : 20,
    total: count(body.total),
    counts: { succeeded: count(counts?.succeeded), failed: count(counts?.failed), imageDeleted: count(counts?.image_deleted) },
    items: untrustedList<IndexTaskWire>(body.items).map(toIndexTask).filter((task): task is IndexTask => task !== null),
  }
}

function toDeletion(value: unknown): ImageDeletion | null {
  const row = untrusted<ImageDeletionWire>(value)
  const imageId = asId(row?.image_id)
  if (!row || !imageId) return null
  return { imageId, imageUrl: safeHttpUrl(row.thumb_url), deletedAt: asText(row.deleted_at) }
}

/** GET /api/image-deletions body; null when it is not the contract's. */
export function toDeletionQueue(payload: unknown): ImageDeletionQueue | null {
  const body = untrusted<ImageDeletionListResponseWire>(payload)
  if (body?.ok !== true || !Array.isArray(body.items)) return null
  const items = untrustedList<ImageDeletionWire>(body.items).map(toDeletion)
    .filter((row): row is ImageDeletion => row !== null)
  return { total: Math.max(count(body.total), items.length), items, cleanupReady: body.cleanup_ready === true }
}

/** POST /api/image-deletions/process body; null when it is not the contract's. */
export function toDeletionRun(payload: unknown): ImageDeletionRun | null {
  const body = untrusted<ImageDeletionProcessResponseWire>(payload)
  if (body?.ok !== true || !Array.isArray(body.processed) || !Array.isArray(body.failed)) return null
  return {
    scanned: count(body.scanned),
    processed: body.processed.map(asId).filter((id): id is string => id !== null),
    failed: untrustedList<ImageDeletionFailureWire>(body.failed).map(item => ({
      imageId: asId(item.image_id) ?? '',
      code: asText(item.error) ?? 'INTERNAL_ERROR',
      detail: asText(item.detail) ?? '清理失败',
    })).filter(item => item.imageId),
    remaining: count(body.remaining),
  }
}
