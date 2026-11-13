import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { ChevronRight, FolderTree, History, LoaderCircle, RotateCcw, Search, X } from 'lucide-react'
import { categoryAncestors, categoryLabel, type CategoryTree } from '../../entities/category'
import { usePreference } from '../../shared/lib'
import { categoryNavView, highlightParts, searchWords, subtreeImageCounts, type CategoryNavRow } from './categoryNav'

/** Every row is this tall, so the list renders only the rows in view (the tree can hold thousands). */
const ROW_HEIGHT = 30
const OVERSCAN = 6
/** Indentation stops growing below this depth, so deep names keep room in the narrow panel. */
const MAX_INDENT_DEPTH = 7
const RECENT_KEY = 'food-atlas-library-recent-categories'
const RECENT_KEPT = 8
const RECENT_SHOWN = 5
// v2: the options start from the new default (hideEmpty on), whatever an earlier visit stored
const OPTIONS_KEY = 'food-atlas-library-category-options-v2'

interface NavPreferences { hideEmpty: boolean; showInactive: boolean }
/**
 * A filter by a category without images finds nothing, and in a catalog of hundreds most
 * categories have none yet: they are left out until asked for.
 */
const DEFAULT_PREFERENCES: NavPreferences = { hideEmpty: true, showInactive: false }

function isNavPreferences(value: unknown): value is NavPreferences {
  const stored = value as Partial<NavPreferences> | null
  return typeof stored === 'object' && stored !== null && typeof stored.hideEmpty === 'boolean' && typeof stored.showInactive === 'boolean'
}

function isIdList(value: unknown): value is number[] {
  return Array.isArray(value) && value.length <= RECENT_KEPT * 2 && value.every(id => Number.isSafeInteger(id) && id > 0)
}

export interface CategoryNavigatorProps {
  tree: CategoryTree | null
  loading: boolean
  /** Why the last read failed; a tree read before stays usable. */
  error: string | null
  onRetry: () => void
  /** The category filter: it and every category below it; null: every category. */
  value: number | null
  onChange: (id: number | null) => void
}

/**
 * The category filter of the library: the tree (GET /api/categories) with each node's image count
 * (its subtree, every scene), a search over names, codes and aliases, and the categories used
 * last. A virtual ARIA tree: arrow keys move and open, Enter picks.
 */
export function CategoryNavigator({ tree, loading, error, onRetry, value, onChange }: CategoryNavigatorProps) {
  const id = useId()
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set())
  const [preferences, setPreferences] = usePreference(OPTIONS_KEY, DEFAULT_PREFERENCES, isNavPreferences)
  const [recent, setRecent] = usePreference<number[]>(RECENT_KEY, [], isIdList)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [list, setList] = useState<HTMLDivElement | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  /** The list's height as laid out; 0 while it has no box (the folded panel of a narrow screen). */
  const [measured, setMeasured] = useState(0)
  const viewport = measured || ROW_HEIGHT * 12
  /** The selection last scrolled into view: a new one is revealed once the rows list it. */
  const revealed = useRef<number | null>(null)

  const counts = useMemo(() => (tree ? subtreeImageCounts(tree) : new Map<number, number>()), [tree])
  /** Categories without any image on them or below them. */
  const emptyCount = useMemo(() => (tree ? [...tree.byId.keys()].filter(nodeId => !counts.get(nodeId)).length : 0), [tree, counts])
  // No category has an image yet (a new library): hiding the empty ones would hide them all, so the option rests
  const canHideEmpty = tree !== null && emptyCount < tree.byId.size
  const hideEmpty = preferences.hideEmpty && canHideEmpty
  const view = useMemo(
    () => (tree ? categoryNavView(tree, counts, { query, expanded, hideEmpty, showInactive: preferences.showInactive }) : null),
    [tree, counts, query, expanded, hideEmpty, preferences.showInactive],
  )
  const rows = useMemo(() => view?.rows ?? [], [view])
  const words = useMemo(() => searchWords(query), [query])
  const indexById = useMemo(() => new Map(rows.map((row, index) => [row.node.id, index])), [rows])
  const activeIndex = activeId !== null ? indexById.get(activeId) ?? -1 : -1
  const enabledCount = useMemo(() => (tree ? [...tree.byId.values()].filter(entry => entry.node.isActive).length : 0), [tree])
  const selected = value !== null ? tree?.byId.get(value)?.node ?? null : null
  const selectedParents = value !== null && tree ? categoryAncestors(tree, value).map(categoryLabel).join(' / ') : ''
  const recentShown = tree ? recent.filter(item => item !== value && tree.byId.has(item)).slice(0, RECENT_SHOWN) : []

  useLayoutEffect(() => {
    if (!list) return
    const measure = () => setMeasured(list.clientHeight)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(list)
    return () => observer.disconnect()
  }, [list])

  function scrollToRow(index: number, center = false) {
    if (!list) return
    const top = index * ROW_HEIGHT
    if (center) list.scrollTop = Math.max(0, top - (list.clientHeight - ROW_HEIGHT) / 2)
    else if (top < list.scrollTop) list.scrollTop = top
    else if (top + ROW_HEIGHT > list.scrollTop + list.clientHeight) list.scrollTop = top + ROW_HEIGHT - list.clientHeight
  }

  // A new search starts at the top, on its first match, without chasing the selection into it;
  // back to the tree, the selection is shown again. Declared before the reveal below: layout
  // effects run in order, so it sees what this one decided in the same commit.
  const searchKey = words.join(' ')
  const lastSearch = useRef(searchKey)
  useLayoutEffect(() => {
    if (lastSearch.current === searchKey) return
    lastSearch.current = searchKey
    if (list) list.scrollTop = 0
    setScrollTop(0)
    if (!searchKey) { revealed.current = null; return }
    revealed.current = value
    const first = rows.find(row => !row.context) ?? rows[0]
    setActiveId(first ? first.node.id : null)
  }, [searchKey])  // only when the words change: rows, list and value are read as they are then

  // A category picked anywhere (a card's chip, the recent list) is scrolled into view once listed
  // and laid out…
  useLayoutEffect(() => {
    if (value === null) { revealed.current = null; return }
    if (revealed.current === value || !list || !measured) return
    const index = indexById.get(value)
    if (index === undefined) return
    revealed.current = value
    setActiveId(value)
    scrollToRow(index, true)
  }, [value, indexById, list, measured])

  // …after its ancestors are opened
  useEffect(() => {
    if (value === null || !tree?.byId.has(value)) return
    const ancestors = categoryAncestors(tree, value).map(node => node.id)
    setExpanded(current => (ancestors.every(ancestor => current.has(ancestor)) ? current : new Set([...current, ...ancestors])))
  }, [value, tree])

  // The first tree read: the domains start open, so their groups show without a click
  const openedRoots = useRef(false)
  useEffect(() => {
    if (openedRoots.current || !tree || tree.roots.length === 0) return
    openedRoots.current = true
    setExpanded(current => new Set([...current, ...tree.roots.map(root => root.node.id)]))
  }, [tree])

  useEffect(() => {
    if (value === null || recent[0] === value) return
    setRecent([value, ...recent.filter(item => item !== value)].slice(0, RECENT_KEPT))
  }, [value, recent, setRecent])

  function toggle(nodeId: number, open?: boolean) {
    setExpanded(current => {
      const isOpen = current.has(nodeId)
      const next = open ?? !isOpen
      if (next === isOpen) return current
      const copy = new Set(current)
      if (next) copy.add(nodeId)
      else copy.delete(nodeId)
      return copy
    })
  }

  /** Filter by it, and open it so its children can narrow further. */
  function pick(row: CategoryNavRow) {
    setActiveId(row.node.id)
    if (row.node.id !== value) onChange(row.node.id)
    if (row.hasChildren && !view?.searching) toggle(row.node.id, true)
  }

  function onTreeKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!rows.length || !view) return
    const index = activeIndex >= 0 ? activeIndex : 0
    const row = rows[index]
    const pageRows = Math.max(1, Math.floor(viewport / ROW_HEIGHT) - 1)
    let next: number | null = null
    switch (event.key) {
      case 'ArrowDown': next = activeIndex < 0 ? 0 : Math.min(rows.length - 1, index + 1); break
      case 'ArrowUp': next = Math.max(0, index - 1); break
      case 'Home': next = 0; break
      case 'End': next = rows.length - 1; break
      case 'PageDown': next = Math.min(rows.length - 1, index + pageRows); break
      case 'PageUp': next = Math.max(0, index - pageRows); break
      case 'ArrowRight':
        if (row.hasChildren && !row.open) toggle(row.node.id, true)
        next = row.open ? index + 1 : index
        break
      case 'ArrowLeft':
        if (row.open && !view.searching) { toggle(row.node.id, false); next = index }
        else next = row.parentId !== null ? indexById.get(row.parentId) ?? index : index
        break
      case 'Enter':
      case ' ':
        pick(row)
        next = index
        break
      default:
        return
    }
    event.preventDefault()
    if (next !== null && rows[next]) {
      setActiveId(rows[next].node.id)
      scrollToRow(next)
    }
  }

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const last = Math.min(rows.length, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN)
  const shown: number[] = []
  for (let index = first; index < last; index += 1) shown.push(index)
  // The active row stays rendered for aria-activedescendant while it is scrolled away
  if (activeIndex >= 0 && (activeIndex < first || activeIndex >= last)) shown.push(activeIndex)

  const emptyText = !view || rows.length
    ? null
    : view.searching
      ? '没有匹配的类别'
      : tree && tree.byId.size > 0
        ? '没有可显示的类别：取消「隐藏没有图片的」或勾选「显示停用的」试试'
        : '还没有类别，可在「食材管理」里新建'

  return <section className="lf-category" aria-labelledby={`${id}-title`}>
    <div className="lf-category-head">
      <h3 className="lf-label" id={`${id}-title`}>类别</h3>
      {tree && <small>{enabledCount} 个启用</small>}
      {loading
        ? <LoaderCircle size={14} className="spin lf-category-busy" aria-label="正在读取类别" />
        : <button type="button" className="lf-icon-button" title="重新读取类别和图片数量" aria-label="重新读取类别和图片数量" onClick={onRetry}><RotateCcw size={14} /></button>}
    </div>

    {value !== null && <div className="lf-current">
      <FolderTree size={15} aria-hidden="true" />
      <span className="lf-current-text" title={[selectedParents, selected ? categoryLabel(selected) : ''].filter(Boolean).join(' / ')}>
        <strong>{selected ? categoryLabel(selected) : `类别 #${value}`}</strong>
        <small>{selected ? selectedParents || '根类别' : '类别目录里没有这个类别'}{selected && !selected.isActive ? ' · 已停用' : ''} · 含下级</small>
      </span>
      <button type="button" className="lf-clear" aria-label="清除类别筛选" title="清除类别筛选" onClick={() => onChange(null)}><X size={14} /></button>
    </div>}

    {recentShown.length > 0 && tree && <div className="lf-recent" role="group" aria-label="最近用过的类别">
      <History size={13} aria-hidden="true" />
      {recentShown.map(item => {
        const node = tree.byId.get(item)!.node
        const parents = categoryAncestors(tree, item).map(categoryLabel).join(' / ')
        return <button type="button" key={item} className="lf-recent-chip" title={parents ? `${parents} / ${categoryLabel(node)}` : categoryLabel(node)} onClick={() => onChange(item)}>{categoryLabel(node)}</button>
      })}
    </div>}

    <div className="lf-input">
      <Search size={14} aria-hidden="true" />
      <input
        type="search"
        aria-label="搜索类别"
        aria-controls={`${id}-tree`}
        placeholder={tree ? '搜索名称、编码或别名' : loading ? '正在读取类别…' : '类别目录不可用'}
        disabled={!tree}
        autoComplete="off"
        value={query}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'ArrowDown' && rows.length) {
            event.preventDefault()
            list?.focus()
          } else if (event.key === 'Enter') {
            event.preventDefault()
            const match = rows.find(row => !row.context)
            if (match) pick(match)
          } else if (event.key === 'Escape' && query) {
            event.preventDefault()
            setQuery('')
          }
        }}
      />
      {query && <button type="button" className="lf-clear" aria-label="清空类别搜索" onClick={() => setQuery('')}><X size={14} /></button>}
    </div>

    <div className="lf-tree-options">
      <label title={canHideEmpty ? `${emptyCount} 个类别还没有图片（含下级也没有）` : '还没有类别带图片，所以全部列出'}>
        <input type="checkbox" checked={hideEmpty} disabled={!canHideEmpty} onChange={event => setPreferences({ ...preferences, hideEmpty: event.target.checked })} />
        隐藏没有图片的{canHideEmpty && emptyCount > 0 && <span className="lf-option-count">{emptyCount}</span>}
      </label>
      <label><input type="checkbox" checked={preferences.showInactive} onChange={event => setPreferences({ ...preferences, showInactive: event.target.checked })} />显示停用的</label>
    </div>

    {error && <p className="lf-error" role="alert">{tree ? `类别没有刷新：${error}` : error} <button type="button" className="mg-text-button" onClick={onRetry}>重试</button></p>}
    {!tree
      ? !error && <div className="lf-tree-status" role="status"><LoaderCircle size={15} className="spin" />正在读取类别…</div>
      : emptyText
        ? <p className="lf-tree-status">{emptyText}</p>
        : <div
          ref={setList}
          id={`${id}-tree`}
          className="lf-tree"
          role="tree"
          aria-label="类别"
          tabIndex={0}
          aria-activedescendant={activeIndex >= 0 ? `${id}-n${rows[activeIndex].node.id}` : undefined}
          // Narrow screens size the box to its rows (capped); wide ones let it fill the column
          style={{ '--lf-rows-height': `${rows.length * ROW_HEIGHT + 2}px` } as CSSProperties}
          onScroll={event => setScrollTop(event.currentTarget.scrollTop)}
          onKeyDown={onTreeKeyDown}
          onFocus={() => {
            if (activeIndex < 0 && rows.length) setActiveId(value !== null && indexById.has(value) ? value : rows[0].node.id)
          }}
        >
          <div className="lf-tree-inner" role="presentation" style={{ height: rows.length * ROW_HEIGHT }}>
            {shown.map(index => {
              const row = rows[index]
              const nodeId = row.node.id
              const label = categoryLabel(row.node)
              const parents = categoryAncestors(tree, nodeId).map(categoryLabel)
              return <div
                key={nodeId}
                id={`${id}-n${nodeId}`}
                role="treeitem"
                aria-level={row.depth + 1}
                aria-posinset={row.position}
                aria-setsize={row.siblings}
                aria-expanded={row.hasChildren ? row.open : undefined}
                aria-selected={nodeId === value}
                aria-label={`${label}${row.node.isActive ? '' : '（停用）'}，${row.count} 张`}
                className={`lf-node${index === activeIndex ? ' is-active' : ''}${row.context ? ' is-context' : ''}`}
                style={{ top: index * ROW_HEIGHT, paddingLeft: 4 + Math.min(row.depth, MAX_INDENT_DEPTH) * 14 }}
                title={`${[...parents, label].join(' / ')}\n${row.node.code} · ${row.count} 张图片（含下级类别，不分场景）`}
                onClick={() => pick(row)}
              >
                <span
                  className={`lf-twisty${row.open ? ' is-open' : ''}${row.hasChildren && !view?.searching ? ' is-toggle' : ''}`}
                  aria-hidden="true"
                  onClick={event => {
                    if (!row.hasChildren || view?.searching) return
                    event.stopPropagation()
                    setActiveId(nodeId)
                    toggle(nodeId)
                  }}
                >{row.hasChildren && <ChevronRight size={14} />}</span>
                <span className="lf-node-label">{highlightParts(label, words).map((part, at) => (part.hit ? <mark key={at}>{part.text}</mark> : <span key={at}>{part.text}</span>))}</span>
                {!row.node.isActive && <span className="lf-off">停用</span>}
                <span className={`lf-node-count${row.count ? '' : ' is-zero'}`}>{row.count}</span>
              </div>
            })}
          </div>
        </div>}

    <p className="lf-tree-foot">
      {view?.searching
        ? <>找到 {view.matched} 个{view.hidden > 0 && <>，另有 {view.hidden} 个被上面的选项隐藏 <button type="button" className="mg-text-button" onClick={() => setPreferences({ hideEmpty: false, showInactive: true })}>全部显示</button></>}</>
        : '点选即筛选（含下级）· 数字为图片数，不分场景'}
    </p>
  </section>
}
