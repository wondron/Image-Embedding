import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { listEnums } from './api'
import { EMPTY_ENUM_CATALOG, enumValueText, toEnumCatalog, type EnumCatalog } from './catalog'

const LOAD_TIMEOUT_MS = 20_000

export interface EnumCatalogState {
  catalog: EnumCatalog
  /** Read GET /api/enums again, e.g. right after a value was appended. */
  refresh: () => void
}

const EnumCatalogContext = createContext<EnumCatalogState>({ catalog: EMPTY_ENUM_CATALOG, refresh: () => {} })

/**
 * Loads GET /api/enums once for the page, so stored enum values are shown with their labels.
 * Loads when `enabled` turns true (e.g. once the backend answers ping), when apiBase changes and
 * on refresh(). A failed load keeps the labels already loaded; values without a label are shown
 * as stored (enumValueText), so a missing catalog never hides data.
 */
export function EnumCatalogProvider({ apiBase, enabled = true, children }: {
  apiBase: string
  enabled?: boolean
  children: ReactNode
}) {
  const [loaded, setLoaded] = useState<{ apiBase: string; catalog: EnumCatalog } | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let active = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS)
    listEnums({ baseUrl: apiBase, signal: controller.signal })
      .then(list => { if (active) setLoaded({ apiBase, catalog: toEnumCatalog(list.enums) }) })
      .catch(() => { /* keep what is loaded: labels are optional, values still show as stored */ })
      .finally(() => window.clearTimeout(timer))
    return () => {
      active = false
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [apiBase, enabled, version])

  const refresh = useCallback(() => setVersion(current => current + 1), [])
  const catalog = loaded?.apiBase === apiBase ? loaded.catalog : EMPTY_ENUM_CATALOG
  const value = useMemo(() => ({ catalog, refresh }), [catalog, refresh])
  return <EnumCatalogContext.Provider value={value}>{children}</EnumCatalogContext.Provider>
}

/** The page's enum catalog; outside a provider: empty, and refresh does nothing. */
export function useEnumCatalog(): EnumCatalogState {
  return useContext(EnumCatalogContext)
}

/** Display text of one stored value of enum `name` (see enumValueText). */
export function useEnumValueText(name: string, value: string | null | undefined): string {
  return enumValueText(useEnumCatalog().catalog, name, value)
}
