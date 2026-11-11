import { useCallback, useEffect, useMemo, useState } from 'react'
import { buildCategoryTree, listCategories, toCategoryFailure, type CategoryNode, type CategoryTree } from '../../entities/category'

const LOAD_TIMEOUT_MS = 20_000

export interface CategoryTreeState {
  /** null until the first successful load for this apiBase. */
  tree: CategoryTree | null
  loading: boolean
  /** Why the last load failed (the tree loaded before stays usable); null otherwise. */
  error: string | null
  refresh: () => void
}

function describe(error: unknown): string {
  const failure = toCategoryFailure(error)
  if (failure.kind === 'uncertain') return '读取类别目录超时或被中断，请重试。'  // a read: nothing to be unsure about
  const extra = [failure.code, failure.traceId ? `trace_id=${failure.traceId}` : null].filter(Boolean).join(' · ')
  return extra ? `${failure.detail}（${extra}）` : failure.detail
}

/**
 * GET /api/categories for a picker. Loads whenever `enabled` turns true (so a page shows the tree
 * as it is now), on refresh() and for a new apiBase; a failed load keeps the tree it had.
 */
export function useCategoryTree(apiBase: string, enabled = true): CategoryTreeState {
  const [loaded, setLoaded] = useState<{ base: string; nodes: CategoryNode[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let active = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(new DOMException('类别接口超时，请稍后重试。', 'TimeoutError')), LOAD_TIMEOUT_MS)
    setLoading(true)
    listCategories({ baseUrl: apiBase, signal: controller.signal })
      .then(list => {
        if (!active) return
        setLoaded({ base: apiBase, nodes: list.categories })
        setError(null)
      })
      .catch(cause => { if (active) setError(describe(cause)) })
      .finally(() => {
        window.clearTimeout(timer)
        if (active) setLoading(false)
      })
    return () => {
      active = false
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [apiBase, enabled, version])

  const nodes = loaded?.base === apiBase ? loaded.nodes : null
  const tree = useMemo(() => (nodes ? buildCategoryTree(nodes) : null), [nodes])
  const refresh = useCallback(() => setVersion(current => current + 1), [])
  return { tree, loading, error, refresh }
}
