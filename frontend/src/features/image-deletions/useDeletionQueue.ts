import { useCallback, useEffect, useRef, useState } from 'react'
import { listImageDeletions, processImageDeletions, type ImageDeletionQueue } from '../../entities/task'
import { ApiError } from '../../shared/api'
import { requestSignal } from '../../shared/lib'
import { CLEAN_BATCH, NO_CLEANUP, addRun, shouldContinue, type CleanupTotals } from './cleanupRun'

/** Rows shown, oldest first (the queue may hold more: `total`). */
export const QUEUE_SHOWN = 50
const REQUEST_TIMEOUT_MS = 30_000

interface QueueSnapshot {
  data: ImageDeletionQueue | null
  loading: boolean
  error: ApiError | null
  readAt: Date | null
}

function asApiError(cause: unknown, fallback: string): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(cause instanceof Error ? cause.message : fallback)
}

/**
 * The deletion queue (GET /api/image-deletions) and its cleanup (POST …/process). A cleanup
 * chains runs while they make progress (cleanupRun.shouldContinue) and then reads the queue again.
 */
export function useDeletionQueue(apiBase: string) {
  const [snapshot, setSnapshot] = useState<QueueSnapshot>({ data: null, loading: true, error: null, readAt: null })
  const [cleaning, setCleaning] = useState(false)
  const lifetime = useRef(new AbortController())
  const sequence = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    return () => controller.abort()
  }, [])

  const load = useCallback(async () => {
    const token = ++sequence.current
    const signal = requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS)
    setSnapshot(previous => ({ ...previous, loading: true, error: null }))
    try {
      const data = await listImageDeletions({ baseUrl: apiBase, limit: QUEUE_SHOWN, signal })
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      setSnapshot({ data, loading: false, error: null, readAt: new Date() })
    } catch (cause) {
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      setSnapshot(previous => ({ ...previous, loading: false, error: asApiError(cause, '读取待清理图片失败，请重试。') }))
    }
  }, [apiBase])

  useEffect(() => { void load() }, [load])

  /** Clean now. Resolves with what was done and, when a run was refused or broke off, why. */
  async function clean(): Promise<{ totals: CleanupTotals; error: ApiError | null } | null> {
    if (cleaning) return null
    setCleaning(true)
    let totals = NO_CLEANUP
    let error: ApiError | null = null
    try {
      for (;;) {
        const run = await processImageDeletions({
          baseUrl: apiBase, limit: CLEAN_BATCH, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS),
        })
        totals = addRun(totals, run)
        if (!shouldContinue(totals, run)) break
      }
    } catch (cause) {
      error = asApiError(cause, '清理请求失败，请重试。')
    }
    if (lifetime.current.signal.aborted) return null
    setCleaning(false)
    void load()
    return { totals, error }
  }

  return { ...snapshot, cleaning, refresh: load, clean }
}
