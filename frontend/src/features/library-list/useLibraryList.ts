import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_SCENE, toApiSceneCode } from '../../entities/scene'
import type { LibraryImageItem } from '../../entities/image'
import { listImages } from '../../lib/images-api'

export type UseLibraryListOptions = {
  enabled: boolean
  apiBase?: string
  page: number
  pageSize?: number
  q?: string
  category?: string | null
  scene: string
  reloadKey?: number
}

export function useLibraryList({
  enabled,
  apiBase = '',
  page,
  pageSize = 24,
  q,
  category,
  scene,
  reloadKey = 0,
}: UseLibraryListOptions) {
  const [items, setItems] = useState<LibraryImageItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [responseScene, setResponseScene] = useState<string>(DEFAULT_SCENE)
  const abortRef = useRef<AbortController | null>(null)
  const apiBaseRef = useRef(apiBase)
  apiBaseRef.current = apiBase

  const reload = useCallback(() => {
    // parent bumps reloadKey
  }, [])

  useEffect(() => {
    if (!enabled) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const requestBase = apiBaseRef.current
    const apiScene = toApiSceneCode(scene, DEFAULT_SCENE)
    setLoading(true)
    setError(null)
    void listImages({
      page,
      pageSize,
      q: q || undefined,
      category: category || undefined,
      scene: apiScene,
      baseUrl: requestBase,
      signal: controller.signal,
    }).then(result => {
      if (controller.signal.aborted) return
      const maxPage = Math.max(1, Math.ceil(result.total / pageSize) || 1)
      if (result.total > 0 && result.items.length === 0 && page > maxPage) {
        setLoading(false)
        return { needPage: maxPage as number }
      }
      setItems(result.items)
      setTotal(result.total)
      setResponseScene(toApiSceneCode(result.scene, DEFAULT_SCENE))
      setConnected(true)
      setLoading(false)
      return null
    }).then(adjust => {
      // page clamp handled by caller via onNeedPage if we expose it — keep simple here
      void adjust
    }).catch(cause => {
      if (controller.signal.aborted || (cause instanceof Error && cause.name === 'AbortError')) return
      setItems([])
      setTotal(0)
      setError(cause instanceof Error ? cause.message : '图库列表加载失败，请重试。')
      setLoading(false)
    })
    return () => controller.abort()
  }, [enabled, page, pageSize, q, category, scene, apiBase, reloadKey])

  return {
    items,
    total,
    loading,
    error,
    connected,
    responseScene,
    reload,
    setError,
  }
}