import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react'
import { ChevronRight, History, LoaderCircle, Search } from 'lucide-react'
import { categoryAncestors, categoryLabel, type CategoryTree } from '../../entities/category'
import { isImeComposing } from '../../shared/lib'
import { categoryOptions, codeTail, filterCategoryOptions, matchedOtherName } from './categoryOptions'
import { pickerIndex, pickerRows } from './pickerRows'
import { loadRecentCategories, rememberCategory } from './recentCategories'
import './category-picker.css'

/** Search results rendered at once; typing narrows the rest. */
const SHOWN = 200
/** Indentation stops growing below this depth, so deep names keep room in a narrow form. */
const MAX_INDENT_DEPTH = 6
const RECENT_SHOWN = 6

interface Entry {
  key: string
  id: number | null
  label: string
  /** 0 for a root, and for every search result. */
  depth: number
  /** false for a disabled category listed only for what is below it. */
  pickable: boolean
  inactive: boolean
  hasChildren: boolean
  open: boolean
  /** Categories that can be picked below it. */
  below: number
  parentKey: string | null
  position: number
  siblings: number
  /** Search results only: where it sits, the name the search matched, and its code. */
  path: string | null
  matched: string | null
  code: string | null
}

export interface CategorySearchProps {
  id: string
  /** Names the list for assistive technology, e.g. 「类别」. */
  label: string
  tree: CategoryTree | null
  loading?: boolean
  /** The current choice. undefined: none yet — with a nullOption, null is a choice of its own. */
  value: number | null | undefined
  onPick: (id: number | null) => void
  disabled?: boolean
  /** Keep the list open (inside a popover); otherwise it opens on focus and closes on pick / blur. */
  alwaysOpen?: boolean
  autoFocus?: boolean
  /** A first entry that picks null, e.g. 「全部类别」 (shown while the query is empty). */
  nullOption?: string
  /** Escape with nothing left to close or clear (a popover closes itself). */
  onEscape?: () => void
  placeholder?: string
  describedBy?: string
  invalid?: boolean
  /** Offer disabled categories too; otherwise one is listed only above an enabled one and cannot be picked. */
  includeInactive?: boolean
  /** Categories left out together with everything below them (keep the set's identity stable). */
  excludeIds?: ReadonlySet<number>
  /** localStorage key of the categories picked last, offered as shortcuts above the list; none when omitted. */
  recentKey?: string
  /** Take the focus when the dialog around it opens (Dialog's data-dialog-initial-focus). */
  dialogInitialFocus?: boolean
}

/**
 * Search input (ARIA combobox) over the category tree, with the list opening in place below it.
 * Nothing typed: the tree itself, folded below the roots (and opened down to the current value),
 * so a catalog of hundreds of categories is browsed level by level instead of scrolled through.
 * Something typed: the matches by name, code, alias or path, best first (categoryOptions), each
 * with where it sits. Arrow keys move, → / ← open and close a level, Enter picks, Escape closes.
 */
export function CategorySearch({
  id, label, tree, loading = false, value, onPick, disabled = false, alwaysOpen = false, autoFocus = false,
  nullOption, onEscape, placeholder, describedBy, invalid = false, includeInactive = false, excludeIds, recentKey,
  dialogInitialFocus = false,
}: CategorySearchProps) {
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const [query, setQuery] = useState('')
  const [openState, setOpenState] = useState(false)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  /** Levels opened or closed by hand; the rest follow defaultOpen. */
  const [toggled, setToggled] = useState<ReadonlyMap<number, boolean>>(() => new Map())
  const [recent, setRecent] = useState<number[]>([])
  /** The highlight moved by keyboard (or the list just opened): bring its row into view. */
  const reveal = useRef<'nearest' | 'center' | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const justOpened = useRef(false)
  const open = alwaysOpen || openState
  const unavailable = !tree
  const searching = query.trim() !== ''

  const index = useMemo(
    () => (tree ? pickerIndex(tree, { includeInactive, exclude: excludeIds }) : null),
    [tree, includeInactive, excludeIds],
  )
  const options = useMemo(() => (tree ? categoryOptions(tree) : []), [tree])
  const matches = useMemo(
    () => (searching && index ? filterCategoryOptions(options, query, index.pickable) : []),
    [searching, index, options, query],
  )
  /** Open unless folded by hand: the roots, and the levels above the current value. */
  const defaultOpen = useMemo(() => {
    const ids = new Set<number>()
    if (!tree) return ids
    for (const root of tree.roots) ids.add(root.node.id)
    if (typeof value === 'number') for (const ancestor of categoryAncestors(tree, value)) ids.add(ancestor.id)
    return ids
  }, [tree, value])
  const rows = useMemo(
    () => (index && !searching ? pickerRows(index, nodeId => toggled.get(nodeId) ?? defaultOpen.has(nodeId)) : []),
    [index, searching, toggled, defaultOpen],
  )
  const entries = useMemo<Entry[]>(() => {
    if (searching) {
      return matches.slice(0, SHOWN).map((option, at) => ({
        key: String(option.id), id: option.id, label: option.label, depth: 0, pickable: true, inactive: !option.isActive,
        hasChildren: false, open: false, below: 0, parentKey: null, position: at + 1, siblings: Math.min(matches.length, SHOWN),
        path: option.path || null, matched: matchedOtherName(option, query), code: option.code,
      }))
    }
    const listed = rows.map<Entry>(row => ({
      key: String(row.node.id), id: row.node.id, label: categoryLabel(row.node), depth: row.depth, pickable: row.pickable,
      inactive: !row.node.isActive, hasChildren: row.hasChildren, open: row.open, below: row.below,
      parentKey: row.parentId === null ? null : String(row.parentId), position: row.position, siblings: row.siblings,
      path: null, matched: null, code: row.node.code,
    }))
    if (!nullOption) return listed
    return [{
      key: 'none', id: null, label: nullOption, depth: 0, pickable: true, inactive: false, hasChildren: false, open: false,
      below: 0, parentKey: null, position: 1, siblings: 1, path: null, matched: null, code: null,
    }, ...listed]
  }, [searching, matches, query, rows, nullOption])
  const found = entries.findIndex(entry => entry.key === activeKey)
  const activeIndex = found >= 0 ? found : 0
  const active = open && entries.length ? entries[activeIndex] : null
  const recentShown = useMemo(() => {
    if (!index || !tree) return []
    return recent
      .filter(item => item !== value && index.pickable.has(item) && tree.byId.has(item))
      .slice(0, RECENT_SHOWN)
  }, [recent, index, tree, value])

  function keyOfValue(): string | null {
    if (value === undefined) return null
    const key = value === null ? (nullOption ? 'none' : null) : String(value)
    return key !== null && entries.some(entry => entry.key === key) ? key : null
  }

  useEffect(() => {
    if (!autoFocus) return
    inputRef.current?.focus()
    // A popover opens on the current value
    const current = keyOfValue()
    if (current) { setActiveKey(current); reveal.current = 'center' }
  }, [autoFocus])  // once, when shown: later changes of value or entries must not move the highlight

  useEffect(() => {
    if (alwaysOpen && recentKey) setRecent(loadRecentCategories(recentKey))
  }, [alwaysOpen, recentKey])

  // Focused while the tree was still being read (a dialog that opens on this field): open once it is there
  const wasUnavailable = useRef(unavailable)
  useEffect(() => {
    const arrived = wasUnavailable.current && !unavailable
    wasUnavailable.current = unavailable
    if (arrived && document.activeElement === inputRef.current) show()
  }, [unavailable])  // only when the tree arrives: show() reads the entries of that render

  useLayoutEffect(() => {
    // Just opened inside a form that scrolls (an aside, a dialog): show as much of the list as fits,
    // keeping the input in view (a box taller than its scroller is aligned at the top)
    if (justOpened.current) {
      justOpened.current = false
      if (open) rootRef.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
    }
    // The highlighted row: scrolls the list only
    const how = reveal.current
    reveal.current = null
    const list = listRef.current
    if (!how || !open || !list || !active) return
    const row = list.querySelector<HTMLElement>(`[data-key="${active.key}"]`)
    if (!row) return
    const top = row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop
    if (how === 'center') list.scrollTop = Math.max(0, top - (list.clientHeight - row.offsetHeight) / 2)
    else if (top < list.scrollTop) list.scrollTop = top
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top + row.offsetHeight - list.clientHeight
  })

  function show() {
    if (disabled || unavailable || alwaysOpen || openState) return
    setOpenState(true)
    setActiveKey(keyOfValue() ?? entries[0]?.key ?? null)
    reveal.current = 'center'
    justOpened.current = true
    if (recentKey) setRecent(loadRecentCategories(recentKey))
  }

  function pick(next: number | null) {
    if (next !== null && recentKey) rememberCategory(recentKey, next)
    onPick(next)
    setQuery('')
    setOpenState(false)
  }

  function toggle(entry: Entry, to?: boolean) {
    if (entry.id === null || !entry.hasChildren) return
    const nodeId = entry.id
    const next = to ?? !entry.open
    if (next === entry.open) return
    setToggled(current => new Map(current).set(nodeId, next))
  }

  /** Enter or a click: pick it; a level that cannot be picked itself opens or closes instead. */
  function choose(entry: Entry) {
    if (entry.pickable) pick(entry.id)
    else toggle(entry)
  }

  function move(to: number) {
    const entry = entries[to]
    if (!entry) return
    setActiveKey(entry.key)
    reveal.current = 'nearest'
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Typing in 拼音: Enter commits the characters, the arrows choose a candidate. Picking a
    // category here would take whatever is highlighted and throw away what was being typed.
    if (isImeComposing(event)) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) { show(); return }
      if (!entries.length) return
      const step = event.key === 'ArrowDown' ? 1 : -1
      move((activeIndex + step + entries.length) % entries.length)
    } else if ((event.key === 'ArrowRight' || event.key === 'ArrowLeft') && open && active && !searching && !query) {
      // The tree's own keys; with something typed they move the caret instead
      event.preventDefault()
      if (event.key === 'ArrowRight') {
        if (active.hasChildren && !active.open) toggle(active, true)
        else if (active.open) move(activeIndex + 1)
      } else if (active.open) toggle(active, false)
      else if (active.parentKey !== null) move(entries.findIndex(entry => entry.key === active.parentKey))
    } else if (event.key === 'Enter') {
      if (open && active) {
        event.preventDefault()
        choose(active)
      }
    } else if (event.key === 'Escape') {
      if (open && !alwaysOpen) {
        event.preventDefault()
        setOpenState(false)
      } else if (query) {
        event.preventDefault()
        setQuery('')
      } else if (onEscape) {
        event.preventDefault()
        onEscape()
      }
    } else if (event.key === 'Home' && open && entries.length && !searching) {
      event.preventDefault()
      move(0)
    } else if (event.key === 'End' && open && entries.length && !searching) {
      event.preventDefault()
      move(entries.length - 1)
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpenState(false)
  }

  const pickableCount = index?.pickable.size ?? 0
  const emptyText = unavailable || entries.length
    ? null
    : searching && pickableCount > 0
      ? (includeInactive ? '没有匹配的类别' : '没有匹配的启用类别')
      : includeInactive ? '还没有可选的类别' : '还没有启用的类别，可在「食材管理」里新建'

  return <div className="cp-search" ref={rootRef} onBlur={onBlur}>
    <div className={`cp-combo${open ? ' is-open' : ''}`}>
      <Search size={15} aria-hidden="true" />
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-haspopup="tree"
        aria-autocomplete="list"
        aria-activedescendant={active ? `${listId}-${active.key}` : undefined}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        autoComplete="off"
        data-dialog-initial-focus={dialogInitialFocus ? true : undefined}
        // Still loading: it can take the focus already, typing waits for the tree. Unreadable: off.
        disabled={disabled || (unavailable && !loading)}
        readOnly={unavailable}
        placeholder={unavailable ? (loading ? '正在读取类别…' : '类别目录不可用') : placeholder ?? '搜索名称、编码或别名'}
        value={query}
        onChange={event => {
          setQuery(event.target.value)
          setActiveKey(null)
          if (listRef.current) listRef.current.scrollTop = 0
          if (!alwaysOpen) setOpenState(true)
        }}
        onFocus={show}
        onClick={show}
        onKeyDown={onKeyDown}
      />
      {loading && <LoaderCircle size={15} className="spin" aria-label="正在读取类别" />}
    </div>
    {open && !searching && recentShown.length > 0 && tree && <div className="cp-recent" role="group" aria-label="最近选过的类别">
      <History size={13} aria-hidden="true" />
      {recentShown.map(item => {
        const node = tree.byId.get(item)!.node
        const parents = categoryAncestors(tree, item).map(categoryLabel).join(' / ')
        return <button
          type="button"
          key={item}
          className="cp-recent-chip"
          tabIndex={-1}
          title={parents ? `${parents} / ${categoryLabel(node)}` : categoryLabel(node)}
          onMouseDown={event => event.preventDefault()}
          onClick={() => pick(item)}
        >{categoryLabel(node)}</button>
      })}
    </div>}
    {open && <ul ref={listRef} className={`cp-list${searching ? ' is-results' : ''}`} id={listId} role="tree" aria-label={`${label}候选`}>
      {entries.map((entry, at) => <li
        key={entry.key}
        id={`${listId}-${entry.key}`}
        data-key={entry.key}
        role="treeitem"
        aria-level={entry.depth + 1}
        aria-posinset={entry.position}
        aria-setsize={entry.siblings}
        aria-expanded={entry.hasChildren ? entry.open : undefined}
        aria-selected={entry.pickable && entry.id === value}
        aria-disabled={entry.pickable ? undefined : true}
        className={`cp-option${at === activeIndex ? ' is-active' : ''}${entry.pickable && entry.id === value ? ' is-selected' : ''}${entry.id === null ? ' is-all' : ''}${entry.pickable ? '' : ' is-context'}`}
        style={entry.depth ? { paddingLeft: `${4 + Math.min(entry.depth, MAX_INDENT_DEPTH) * 16}px` } : undefined}
        title={entry.code ? [
          `${entry.path ? `${entry.path} / ` : ''}${entry.label}`,
          entry.code,
          entry.hasChildren ? `下面有 ${entry.below} 个可选的类别` : null,
          entry.pickable ? null : '已停用，不能选；可以展开选它下面的类别',
        ].filter(Boolean).join('\n') : undefined}
        onMouseDown={event => event.preventDefault()}
        onMouseMove={() => { if (entry.key !== active?.key) setActiveKey(entry.key) }}
        onClick={() => choose(entry)}
      >
        {!searching && <span
          className={`cp-twisty${entry.open ? ' is-open' : ''}${entry.hasChildren ? ' is-toggle' : ''}`}
          aria-hidden="true"
          onClick={event => {
            if (!entry.hasChildren) return
            event.stopPropagation()
            setActiveKey(entry.key)
            toggle(entry)
          }}
        >{entry.hasChildren && <ChevronRight size={14} />}</span>}
        <span className="cp-option-label">{entry.label}</span>
        {entry.matched && <span className="cp-option-matched">{entry.matched}</span>}
        {entry.inactive && <span className="cp-off">停用</span>}
        {entry.path && <span className="cp-option-path">{entry.path}</span>}
        {searching
          ? entry.code && <code className="cp-option-code">{codeTail(entry.code)}</code>
          : entry.hasChildren && <span className="cp-option-count" aria-hidden="true">{entry.below}</span>}
      </li>)}
      {emptyText && <li className="cp-empty" role="none">{emptyText}</li>}
      {matches.length > SHOWN && <li className="cp-more" role="none">还有 {matches.length - SHOWN} 个，继续输入以缩小范围</li>}
    </ul>}
  </div>
}
