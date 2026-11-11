import { ApiError, requestJsonResult, type ApiLabels, type IndexTaskClearResponseWire, type IndexTaskListResponseWire } from '../../shared/api'
import { asInteger, untrusted } from '../../shared/lib/parse'
import {
  toDeletionQueue,
  toDeletionRun,
  toIndexTaskPage,
  type ImageDeletionQueue,
  type ImageDeletionRun,
  type IndexTaskPage,
  type IndexTaskStatus,
} from './model'

const TASK_LABELS: ApiLabels = { service: '任务服务', endpoint: '任务接口', action: '读取入库记录' }
const CLEAR_LABELS: ApiLabels = { service: '任务服务', endpoint: '任务接口', action: '清空入库记录' }
const QUEUE_LABELS: ApiLabels = { service: '任务服务', endpoint: '清理接口', action: '读取待清理图片' }
const PROCESS_LABELS: ApiLabels = { service: '任务服务', endpoint: '清理接口', action: '清理图片' }

/** At most this many records per page (backend MAX_PAGE_SIZE). */
export const MAX_TASK_PAGE_SIZE = 100
/** At most this many queued deletions per read or cleanup run (backend MAX_DELETIONS). */
export const MAX_DELETION_BATCH = 200

export interface TaskRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

export interface ListIndexTasksOptions extends TaskRequestOptions {
  /** null / undefined: every record. */
  status?: IndexTaskStatus | null
  page?: number
  pageSize?: number
}

function unreadable(status: number, traceId: string | null): ApiError {
  return new ApiError('任务接口返回的数据格式不正确，请刷新后重试。', { status, traceId, code: 'TASK_PROTOCOL_ERROR' })
}

/** GET /api/index-tasks: one record per indexed file, newest first. */
export async function listIndexTasks({ status, page = 1, pageSize = 20, baseUrl, signal }: ListIndexTasksOptions = {}): Promise<IndexTaskPage> {
  const query = new URLSearchParams({ page: String(page), page_size: String(Math.min(pageSize, MAX_TASK_PAGE_SIZE)) })
  if (status) query.set('status', status)
  const result = await requestJsonResult('/api/index-tasks', { baseUrl, signal, query, labels: TASK_LABELS })
  const body = untrusted<IndexTaskListResponseWire>(result.body)
  const counts = untrusted<Record<IndexTaskStatus, number>>(body?.counts)
  const nonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
  const ids = new Set<number>()
  // Validate before the existing mapper can default numbers or drop malformed rows.
  if (body?.ok !== true || !Array.isArray(body.items)
    || !nonnegative(body.total) || !nonnegative(counts?.succeeded) || !nonnegative(counts?.failed)
    || !nonnegative(body.page) || body.page < 1 || body.page !== page
    || !nonnegative(body.page_size) || body.page_size < 1 || body.page_size !== Math.min(pageSize, MAX_TASK_PAGE_SIZE)
    || body.status !== (status ?? null)) throw unreadable(result.status, result.traceId)
  for (const raw of body.items) {
    const item = untrusted<{ id: number }>(raw)
    if (!nonnegative(item?.id) || item.id < 1 || ids.has(item.id)) throw unreadable(result.status, result.traceId)
    ids.add(item.id)
  }
  const parsed = toIndexTaskPage(result.body)
  if (!parsed || parsed.items.length !== body.items.length) throw unreadable(result.status, result.traceId)
  return { ...parsed, readTraceId: result.traceId }
}

/** DELETE /api/index-tasks?status=: only the records go, never the images. Returns how many. */
export async function clearIndexTasks(status: IndexTaskStatus, { baseUrl, signal }: TaskRequestOptions = {}): Promise<number> {
  const result = await requestJsonResult('/api/index-tasks', {
    baseUrl, signal, method: 'DELETE', query: new URLSearchParams({ status }), labels: CLEAR_LABELS,
  })
  const body = untrusted<IndexTaskClearResponseWire>(result.body)
  const deleted = asInteger(body?.deleted)
  if (body?.ok !== true || deleted === null || deleted < 0) throw unreadable(result.status, result.traceId)
  return deleted
}

/** GET /api/image-deletions: deleted images whose point and files still wait, oldest first. */
export async function listImageDeletions({ limit = 50, baseUrl, signal }: TaskRequestOptions & { limit?: number } = {}): Promise<ImageDeletionQueue> {
  const query = new URLSearchParams({ limit: String(Math.min(limit, MAX_DELETION_BATCH)) })
  const result = await requestJsonResult('/api/image-deletions', { baseUrl, signal, query, labels: QUEUE_LABELS })
  const parsed = toDeletionQueue(result.body)
  if (!parsed) throw unreadable(result.status, result.traceId)
  return parsed
}

/** POST /api/image-deletions/process: clean the oldest `limit` now; failures stay queued. */
export async function processImageDeletions({ limit = 50, baseUrl, signal }: TaskRequestOptions & { limit?: number } = {}): Promise<ImageDeletionRun> {
  const result = await requestJsonResult('/api/image-deletions/process', {
    baseUrl, signal, method: 'POST', json: { limit: Math.min(limit, MAX_DELETION_BATCH) }, labels: PROCESS_LABELS,
  })
  const parsed = toDeletionRun(result.body)
  if (!parsed) throw unreadable(result.status, result.traceId)
  return parsed
}
