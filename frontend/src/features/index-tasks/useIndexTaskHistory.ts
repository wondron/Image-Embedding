import { useEffect, useRef, useState } from 'react'
import { listIndexTasks, type IndexTaskImageState, type IndexTaskPage, type IndexTaskStatus } from '../../entities/task'
import { ApiError } from '../../shared/api'

export const TASK_PAGE_SIZE = 20

/**
 * What the history shows. Every record is in exactly one view besides 全部. The succeeded records
 * split in three: 新入库 (the upload stored a new image, still in the library), 重复上传 (the image
 * was already stored: nothing was added) and 图片已删除 (the image was deleted since; its record
 * is kept). Only 新入库 counts images: the other two would count one image twice or not at all.
 */
export type TaskView = 'all' | 'stored' | 'repeated' | 'deleted' | 'failed'
export const TASK_VIEWS: Readonly<Record<TaskView, { status: IndexTaskStatus | null; imageState: IndexTaskImageState | null; newImage: boolean | null }>> = {
  all: { status: null, imageState: null, newImage: null },
  stored: { status: 'succeeded', imageState: 'present', newImage: true },
  repeated: { status: 'succeeded', imageState: 'present', newImage: false },
  deleted: { status: 'succeeded', imageState: 'deleted', newImage: null },
  failed: { status: 'failed', imageState: null, newImage: null },
}

interface Query { view: TaskView; page: number; revision: number; corrected: boolean }
interface Snapshot { key: string; data: IndexTaskPage | null; loading: boolean; error: ApiError | null; readAt: Date | null; changed: boolean }

export function useIndexTaskHistory(apiBase: string) {
  const [query, setQuery] = useState<Query>({ view: 'all', page: 1, revision: 0, corrected: false })
  const key = JSON.stringify([apiBase, query.view, query.page])
  const [snapshot, setSnapshot] = useState<Snapshot>({ key, data: null, loading: true, error: null, readAt: null, changed: false })
  const sequence = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const inFlight = useRef(false)
  const visible: Snapshot = snapshot.key === key ? snapshot : { key, data: null, loading: true, error: null, readAt: null, changed: false }

  useEffect(() => {
    const token = ++sequence.current
    const abort = new AbortController()
    controller.current = abort
    inFlight.current = true
    const current = () => sequence.current === token && !abort.signal.aborted
    setSnapshot(previous => previous.key === key
      ? { ...previous, loading: true, error: null, changed: false }
      : { key, data: null, loading: true, error: null, readAt: null, changed: false })
    void (async () => {
      try {
        const data = await listIndexTasks({ baseUrl: apiBase, ...TASK_VIEWS[query.view], page: query.page, pageSize: TASK_PAGE_SIZE, signal: abort.signal })
        if (!current()) return
        const lastPage = Math.max(1, Math.ceil(data.total / TASK_PAGE_SIZE))
        const correctedPage = data.total === 0 ? 1 : Math.min(query.page, lastPage)
        // Exactly one correction per user query/refresh, even if concurrent changes continue.
        if (!query.corrected && correctedPage !== query.page) {
          setQuery(previous => previous === query ? { ...query, page: correctedPage, corrected: true } : previous)
          return
        }
        const changed = query.page !== correctedPage || (data.total > 0 && data.items.length === 0)
        setSnapshot({ key, data, loading: false, error: null, readAt: new Date(), changed })
      } catch (cause) {
        if (!current()) return
        const error = cause instanceof ApiError ? cause : new ApiError(cause instanceof Error ? cause.message : '读取入库记录失败，请重试。')
        setSnapshot(previous => previous.key === key
          ? { ...previous, loading: false, error }
          : { key, data: null, loading: false, error, readAt: null, changed: false })
      } finally {
        if (sequence.current === token) inFlight.current = false
      }
    })()
    return () => {
      abort.abort()
      if (sequence.current === token) { sequence.current++; inFlight.current = false }
    }
  }, [apiBase, key, query])

  function invalidate() { sequence.current++; controller.current?.abort(); inFlight.current = false }
  function filter(view: TaskView) {
    if (view === query.view) return
    invalidate(); setQuery(previous => ({ view, page: 1, revision: previous.revision + 1, corrected: false }))
  }
  function page(page: number) {
    if (visible.loading || page < 1 || page === query.page) return
    invalidate(); setQuery(previous => ({ ...previous, page, revision: previous.revision + 1, corrected: false }))
  }
  function refresh() {
    if (visible.loading || inFlight.current) return
    reload()
  }
  /** After records were deleted: read again even while a read is in flight (it may predate the delete). */
  function reload() {
    invalidate(); inFlight.current = true
    setQuery(previous => ({ ...previous, revision: previous.revision + 1, corrected: false }))
  }
  return { ...visible, query, filter, page, refresh, reload }
}
