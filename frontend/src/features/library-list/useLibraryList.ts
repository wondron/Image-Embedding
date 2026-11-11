import { useEffect, useRef, useState } from 'react'
import { DEFAULT_SCENE, toApiSceneCode, unknownScene, sceneError } from '../../entities/scene'
import { refreshSceneCatalog } from '../scene-catalog'
import { listImages, type LabelStatus, type LibraryImageItem } from '../../entities/image'

export type UseLibraryListOptions = {
  enabled: boolean
  apiBase?: string
  page: number
  pageSize?: number
  q?: string
  /** That category and its subtree. */
  categoryId?: number | null
  labelStatus?: LabelStatus | null
  scene: string
  /** Bump to refetch the same page (retry). */
  reloadKey?: number
}

/**
 * One library page from GET /api/images. Refetches when any option changes and aborts the
 * superseded request. A page past the end comes back with total > 0 and no items; the caller
 * clamps the page.
 */
export function useLibraryList({
  enabled,
  apiBase = '',
  page,
  pageSize = 24,
  q,
  categoryId = null,
  labelStatus = null,
  scene,
  reloadKey = 0,
}: UseLibraryListOptions) {
  const [items, setItems] = useState<LibraryImageItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)

  const failedRequest = useRef<string | null>(null)
  useEffect(() => {
    const requestKey = JSON.stringify([apiBase, page, pageSize, q, categoryId, labelStatus, scene, reloadKey])
    if (!enabled) { setLoading(false); return }
    if (failedRequest.current === requestKey) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    listImages({
      page,
      pageSize,
      q: q || undefined,
      categoryId,
      labelStatus,
      scene: toApiSceneCode(scene, DEFAULT_SCENE),
      baseUrl: apiBase,
      signal: controller.signal,
    }).then(result => {
      if (controller.signal.aborted) return
      setItems(result.items)
      setTotal(result.total)
      failedRequest.current = null
      setConnected(true)
      setLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted || (cause instanceof Error && cause.name === 'AbortError')) return
      failedRequest.current = requestKey
      if (unknownScene(cause)) void refreshSceneCatalog(apiBase, true)
      setError(sceneError(cause))
      setLoading(false)
    })
    return () => controller.abort()
  }, [enabled, page, pageSize, q, categoryId, labelStatus, scene, apiBase, reloadKey])

  return { items, total, loading, error, connected }
}
