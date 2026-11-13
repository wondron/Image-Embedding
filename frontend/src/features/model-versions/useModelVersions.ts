import { useCallback, useEffect, useRef, useState } from 'react'
import { buildActive, listEmbeddingModels, type ModelBuild, type ModelOverview } from '../../entities/embedding-model'
import { ApiError } from '../../shared/api'
import { requestSignal } from '../../shared/lib'

/** How often the page reads the versions again while a build runs. */
export const BUILD_POLL_MS = 2000
const REQUEST_TIMEOUT_MS = 30_000

interface Snapshot {
  data: ModelOverview | null
  loading: boolean
  error: ApiError | null
}

function asApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(cause instanceof Error ? cause.message : '读取模型版本失败，请重试。')
}

/**
 * GET /api/embedding-models, read again every BUILD_POLL_MS while a build runs. onBuildEnd is told
 * once when a build this page saw running has ended (to say how it went).
 */
export function useModelVersions(apiBase: string, onBuildEnd?: (build: ModelBuild, overview: ModelOverview) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ data: null, loading: true, error: null })
  const lifetime = useRef(new AbortController())
  const sequence = useRef(0)
  const running = useRef<{ modelId: number; startedAt: string } | null>(null)
  const ended = useRef(onBuildEnd)
  ended.current = onBuildEnd

  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    return () => controller.abort()
  }, [])

  const load = useCallback(async () => {
    const token = ++sequence.current
    setSnapshot(previous => ({ ...previous, loading: true, error: null }))
    try {
      const data = await listEmbeddingModels({ baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) })
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      const build = data.build
      const seen = running.current
      if (seen && build && build.modelId === seen.modelId && build.startedAt === seen.startedAt && !buildActive(build)) {
        running.current = null
        ended.current?.(build, data)
      }
      if (buildActive(build)) running.current = { modelId: build!.modelId, startedAt: build!.startedAt }
      else if (seen && (!build || build.startedAt !== seen.startedAt)) running.current = null
      setSnapshot({ data, loading: false, error: null })
    } catch (cause) {
      if (token !== sequence.current || lifetime.current.signal.aborted) return
      setSnapshot(previous => ({ ...previous, loading: false, error: asApiError(cause) }))
    }
  }, [apiBase])

  useEffect(() => { void load() }, [load])

  const active = buildActive(snapshot.data?.build ?? null)
  useEffect(() => {
    if (!active || snapshot.loading) return
    const timer = window.setTimeout(() => void load(), BUILD_POLL_MS)
    return () => window.clearTimeout(timer)
  }, [active, snapshot.loading, snapshot.data, load])

  return { ...snapshot, refresh: load }
}
