import { useCallback, useEffect, useRef, useState } from 'react'
import {
  checkActive,
  getConsistencyCheck,
  startConsistencyCheck,
  stopConsistencyCheck,
  type CheckOptions,
  type ConsistencyRun,
} from '../../entities/consistency'
import { ApiError } from '../../shared/api'
import { requestSignal } from '../../shared/lib'

/** How often the page reads the check again while it runs. */
export const CHECK_POLL_MS = 1500
const REQUEST_TIMEOUT_MS = 30_000

interface Snapshot {
  /** undefined: not read yet; null: nothing checked in this process yet. */
  run: ConsistencyRun | null | undefined
  loading: boolean
  error: ApiError | null
}

function asApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(cause instanceof Error ? cause.message : '读取数据核对失败，请重试。')
}

/**
 * GET /api/consistency, read again every CHECK_POLL_MS while a check runs; start and stop.
 * onEnd is told once when a check this page saw running has ended.
 */
export function useConsistencyCheck(apiBase: string, onEnd?: (run: ConsistencyRun) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ run: undefined, loading: true, error: null })
  const lifetime = useRef(new AbortController())
  const sequence = useRef(0)
  const watching = useRef<string | null>(null)
  const ended = useRef(onEnd)
  ended.current = onEnd

  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    return () => controller.abort()
  }, [])

  const accept = useCallback((run: ConsistencyRun | null) => {
    const seen = watching.current
    if (run && checkActive(run)) watching.current = run.startedAt
    else {
      watching.current = null
      if (run && seen === run.startedAt) ended.current?.(run)
    }
    setSnapshot({ run, loading: false, error: null })
  }, [])

  const load = useCallback(async () => {
    const token = ++sequence.current
    setSnapshot(previous => ({ ...previous, loading: true, error: null }))
    try {
      const run = await getConsistencyCheck({ baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) })
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      accept(run)
    } catch (cause) {
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      setSnapshot(previous => ({ ...previous, loading: false, error: asApiError(cause) }))
    }
  }, [apiBase, accept])

  useEffect(() => { void load() }, [load])

  const active = checkActive(snapshot.run ?? null)
  useEffect(() => {
    if (!active || snapshot.loading) return
    const timer = window.setTimeout(() => void load(), CHECK_POLL_MS)
    return () => window.clearTimeout(timer)
  }, [active, snapshot.loading, snapshot.run, load])

  /** Starts a check; throws ApiError when the server refuses (one is already running, no database). */
  const start = useCallback(async (options: CheckOptions) => {
    sequence.current++  // a read in flight must not overwrite the new run
    const run = await startConsistencyCheck(options, { baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) })
    if (!lifetime.current.signal.aborted) accept(run)
  }, [apiBase, accept])

  const stop = useCallback(async () => {
    sequence.current++
    const run = await stopConsistencyCheck({ baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) })
    if (!lifetime.current.signal.aborted) accept(run)
  }, [apiBase, accept])

  return { ...snapshot, active, refresh: load, start, stop }
}
