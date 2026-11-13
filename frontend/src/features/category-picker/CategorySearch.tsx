import { useEffect, useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react'
import { LoaderCircle, Search } from 'lucide-react'
import type { CategoryTree } from '../../entities/category'
import { categoryOptions, filterCategoryOptions } from './categoryOptions'
import './category-picker.css'

/** Options rendered at once; typing narrows the rest. */
const SHOWN = 200

interface Entry {
  key: string
  id: number | null
  label: string
  /** The path, shown while searching (in tree order the indentation says it). */
  path: string | null
  code: string | null
  depth: number
}

export interface CategorySearchProps {
  id: string
  /** Names the list for assistive technology, e.g. 「类别」. */
  label: string
  tree: CategoryTree | null
  loading?: boolean
  value: number | null
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
}

/**
 * Search input (ARIA combobox) + listbox over the category tree: active categories only, matched
 * by name, code, alias or path (categoryOptions). Arrow keys move, Enter picks, Escape closes.
 */
export function CategorySearch({
  id, label, tree, loading = false, value, onPick, disabled = false, alwaysOpen = false, autoFocus = false,
  nullOption, onEscape, placeholder, describedBy, invalid = false,
}: CategorySearchProps) {
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [openState, setOpenState] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const open = alwaysOpen || openState
  const unavailable = !tree

  const options = useMemo(() => (tree ? categoryOptions(tree) : []), [tree])
  const matches = useMemo(() => filterCategoryOptions(options, query), [options, query])
  const searching = query.trim() !== ''
  const entries = useMemo<Entry[]>(() => [
    ...(nullOption && !searching ? [{ key: 'none', id: null, label: nullOption, path: null, code: null, depth: 0 }] : []),
    ...matches.slice(0, SHOWN).map(option => ({
      key: String(option.id),
      id: option.id,
      label: option.label,
      path: searching ? option.path || null : null,
      code: option.code,
      depth: searching ? 0 : option.depth,
    })),
  ], [matches, nullOption, searching])
  const active = open && entries.length ? entries[Math.min(activeIndex, entries.length - 1)] : null

  useEffect(() => {
    if (!autoFocus) return
    inputRef.current?.focus()
    // A popover opens on the current value
    const current = entries.findIndex(entry => entry.id === value)
    if (current >= 0) setActiveIndex(current)
  }, [autoFocus])  // once, when shown: later changes of value or entries must not move the highlight

  function show() {
    if (disabled || unavailable || alwaysOpen) return
    setOpenState(true)
    const current = entries.findIndex(entry => entry.id === value)
    setActiveIndex(current >= 0 ? current : 0)
  }

  function pick(next: number | null) {
    onPick(next)
    setQuery('')
    setOpenState(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) { show(); return }
      if (!entries.length) return
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex(current => (Math.min(current, entries.length - 1) + step + entries.length) % entries.length)
    } else if (event.key === 'Enter') {
      if (open && active) {
        event.preventDefault()
        pick(active.id)
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
    } else if (event.key === 'Home' && open && entries.length && searching === false) {
      event.preventDefault()
      setActiveIndex(0)
    } else if (event.key === 'End' && open && entries.length && searching === false) {
      event.preventDefault()
      setActiveIndex(entries.length - 1)
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpenState(false)
  }

  return <div className="cp-search" onBlur={onBlur}>
    <div className={`cp-combo${open ? ' is-open' : ''}`}>
      <Search size={15} aria-hidden="true" />
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active ? `${listId}-${active.key}` : undefined}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        autoComplete="off"
        disabled={disabled || unavailable}
        placeholder={unavailable ? (loading ? '正在读取类别…' : '类别目录不可用') : placeholder ?? '搜索名称、编码或别名'}
        value={query}
        onChange={event => { setQuery(event.target.value); setActiveIndex(0); if (!alwaysOpen) setOpenState(true) }}
        onFocus={show}
        onClick={show}
        onKeyDown={onKeyDown}
      />
      {loading && <LoaderCircle size={15} className="spin" aria-label="正在读取类别" />}
    </div>
    {open && <ul className="cp-list" id={listId} role="listbox" aria-label={`${label}候选`}>
      {entries.map(entry => <li
        key={entry.key}
        id={`${listId}-${entry.key}`}
        role="option"
        aria-selected={entry.id === value}
        className={`cp-option${entry.key === active?.key ? ' is-active' : ''}${entry.id === value ? ' is-selected' : ''}${entry.id === null ? ' is-all' : ''}`}
        style={entry.depth ? { paddingLeft: `${10 + Math.min(entry.depth, 6) * 14}px` } : undefined}
        onMouseDown={event => event.preventDefault()}
        onMouseMove={() => { const index = entries.indexOf(entry); if (index !== activeIndex) setActiveIndex(index) }}
        onClick={() => pick(entry.id)}
      >
        <span>{entry.label}</span>
        {entry.code && <small>{entry.path ? `${entry.path} · ` : ''}<code>{entry.code}</code></small>}
      </li>)}
      {!matches.length && !unavailable && <li className="cp-empty" role="presentation">{options.some(option => option.isActive) ? '没有匹配的启用类别' : '还没有启用的类别，可在「食材管理」里新建'}</li>}
      {matches.length > SHOWN && <li className="cp-more" role="presentation">还有 {matches.length - SHOWN} 个，继续输入以缩小范围</li>}
    </ul>}
  </div>
}
