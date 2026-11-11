import { useEffect, useRef, useState } from 'react'
import { listIndexTasks, type IndexTaskPage, type IndexTaskStatus } from '../../entities/task'
import { ApiError } from '../../shared/api'

export const TASK_PAGE_SIZE = 20
interface Query { status: IndexTaskStatus | null; page: number; revision: number; corrected: boolean }
interface Snapshot { key: string; data: IndexTaskPage | null; loading: boolean; error: ApiError | null; readAt: Date | null; changed: boolean }

export function useIndexTaskHistory(apiBase: string) {
  const [query, setQuery] = useState<Query>({ status: null, page: 1, revision: 0, corrected: false })
  const key = JSON.stringify([apiBase, query.status, query.page])
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
        const data = await listIndexTasks({ baseUrl: apiBase, status: query.status, page: query.page, pageSize: TASK_PAGE_SIZE, signal: abort.signal })
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
  function filter(status: IndexTaskStatus | null) {
    if (status === query.status) return
    invalidate(); setQuery(previous => ({ status, page: 1, revision: previous.revision + 1, corrected: false }))
  }
  function page(page: number) {
    if (visible.loading || page < 1 || page === query.page) return
    invalidate(); setQuery(previous => ({ ...previous, page, revision: previous.revision + 1, corrected: false }))
  }
  function refresh() {
    if (visible.loading || inFlight.current) return
    invalidate(); inFlight.current = true
    setQuery(previous => ({ ...previous, revision: previous.revision + 1, corrected: false }))
  }
  return { ...visible, query, filter, page, refresh }
}
