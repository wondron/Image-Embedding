import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { LibraryImageItem } from '../../entities/image'

/**
 * The images picked for a batch action. Picks stay while paging; other filters mean another result
 * set, so the picks are dropped then (nothing hidden ever goes into a batch). The picked images'
 * data follows the reloaded list (a batch edit changes their tags or category).
 */
export function useLibrarySelection(pageItems: readonly LibraryImageItem[], resetKey: string) {
  const [selected, setSelected] = useState<ReadonlyMap<string, LibraryImageItem>>(() => new Map())
  /** The last image picked or unpicked: Shift+click works from it. */
  const anchor = useRef<string | null>(null)
  const lastKey = useRef(resetKey)

  useEffect(() => {
    if (lastKey.current === resetKey) return
    lastKey.current = resetKey
    anchor.current = null
    setSelected(current => (current.size ? new Map() : current))
  }, [resetKey])

  useEffect(() => {
    setSelected(current => {
      let next: Map<string, LibraryImageItem> | null = null
      for (const item of pageItems) {
        if (current.has(item.id) && current.get(item.id) !== item) {
          next ??= new Map(current)
          next.set(item.id, item)
        }
      }
      return next ?? current
    })
  }, [pageItems])

  /** Pick or unpick one; range: everything on this page from the last one picked takes its new state. */
  const toggle = useCallback((item: LibraryImageItem, range = false) => {
    const from = anchor.current
    anchor.current = item.id
    setSelected(current => {
      const pick = !current.has(item.id)
      const start = range && from !== null ? pageItems.findIndex(entry => entry.id === from) : -1
      const end = pageItems.findIndex(entry => entry.id === item.id)
      const targets = start >= 0 && end >= 0 ? pageItems.slice(Math.min(start, end), Math.max(start, end) + 1) : [item]
      const next = new Map(current)
      for (const target of targets) {
        if (pick) next.set(target.id, target)
        else next.delete(target.id)
      }
      return next
    })
  }, [pageItems])

  /** Pick (true) or unpick (false) every image of this page. */
  const setPage = useCallback((pick: boolean) => {
    setSelected(current => {
      const next = new Map(current)
      for (const item of pageItems) {
        if (pick) next.set(item.id, item)
        else next.delete(item.id)
      }
      return next
    })
  }, [pageItems])

  const add = useCallback((items: readonly LibraryImageItem[]) => {
    setSelected(current => {
      const next = new Map(current)
      for (const item of items) next.set(item.id, item)
      return next
    })
  }, [])

  const remove = useCallback((ids: Iterable<string>) => {
    const gone = new Set(ids)
    if (!gone.size) return
    setSelected(current => new Map([...current].filter(([id]) => !gone.has(id))))
  }, [])

  const clear = useCallback(() => {
    anchor.current = null
    setSelected(current => (current.size ? new Map() : current))
  }, [])

  const ids = useMemo(() => new Set(selected.keys()), [selected])
  const onPage = pageItems.filter(item => ids.has(item.id)).length
  return {
    selected,
    ids,
    /** Every image of this page is picked / some are. */
    pageAll: pageItems.length > 0 && onPage === pageItems.length,
    pageSome: onPage > 0,
    toggle,
    setPage,
    add,
    remove,
    clear,
  }
}
