import { useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react'
import { LoaderCircle, RotateCcw, Search, X } from 'lucide-react'
import type { CategoryTree } from '../../entities/category'
import { categoryOptions, categoryPath, filterCategoryOptions } from './categoryOptions'
import './category-picker.css'

/** Options rendered at once; typing narrows the rest. */
const SHOWN = 200

export interface CategoryPickerProps {
  id: string
  label: string
  tree: CategoryTree | null
  loading?: boolean
  /** Why the tree could not be read; shown with a retry. */
  loadError?: string | null
  onRetry?: () => void
  value: number | null
  onChange: (id: number | null) => void
  disabled?: boolean
  /** A problem with the current value (e.g. the category was disabled meanwhile). */
  error?: string
  /** Shown under the field when there is no error. */
  hint?: string
}

/**
 * Searchable category select (ARIA combobox + listbox) over GET /api/categories: active
 * categories only, matched by name, code, alias or path, shown with their path. The list opens
 * in place (not as an overlay), so it works inside panels that clip and inside dialogs.
 */
export function CategoryPicker({
  id, label, tree, loading = false, loadError, onRetry, value, onChange, disabled = false, error, hint,
}: CategoryPickerProps) {
  const listId = useId()
  const hintId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)

  const options = useMemo(() => (tree ? categoryOptions(tree) : []), [tree])
  const matches = useMemo(() => filterCategoryOptions(options, query), [options, query])
  const shown = matches.slice(0, SHOWN)
  const selected = value !== null && tree ? tree.byId.get(value)?.node ?? null : null
  const selectedPath = value !== null && tree ? categoryPath(tree, value) : null
  const unavailable = !tree
  const active = open && shown.length ? shown[Math.min(activeIndex, shown.length - 1)] : null

  function pick(next: number | null) {
    onChange(next)
    setQuery('')
    setOpen(false)
    inputRef.current?.focus()
  }

  function show() {
    if (disabled || unavailable) return
    setOpen(true)
    // Start on the current category when it is listed, else on the first match
    const current = value === null ? -1 : shown.findIndex(option => option.id === value)
    setActiveIndex(current >= 0 ? current : 0)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) { show(); return }
      if (!shown.length) return
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex(current => (Math.min(current, shown.length - 1) + step + shown.length) % shown.length)
    } else if (event.key === 'Enter') {
      if (open && active) {
        event.preventDefault()
        pick(active.id)
      }
    } else if (event.key === 'Escape') {
      if (open) {
        event.preventDefault()
        setOpen(false)
      } else if (query) {
        event.preventDefault()
        setQuery('')
      }
    } else if (event.key === 'Home' && open && shown.length) {
      event.preventDefault()
      setActiveIndex(0)
    } else if (event.key === 'End' && open && shown.length) {
      event.preventDefault()
      setActiveIndex(shown.length - 1)
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!rootRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
  }

  const describedBy = error || loadError || hint ? hintId : undefined
  return <div className={`cp${error ? ' is-invalid' : ''}`} ref={rootRef} onBlur={onBlur}>
    <label className="cp-label" htmlFor={id}>{label}</label>
    {value !== null && <div className="cp-selected">
      <div>
        <strong>{selected ? (selected.name ?? selected.code) : `类别 #${value}`}</strong>
        {selectedPath && selectedPath !== (selected?.name ?? selected?.code) && <small>{selectedPath}</small>}
        {selected && <code>{selected.code}</code>}
      </div>
      <button type="button" className="cp-clear" disabled={disabled} aria-label={`清除类别 ${selected?.name ?? selected?.code ?? ''}`.trim()} onClick={() => pick(null)}><X size={14} /></button>
    </div>}
    <div className={`cp-combo${open ? ' is-open' : ''}`}>
      <Search size={15} aria-hidden="true" />
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active ? `${listId}-${active.id}` : undefined}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        autoComplete="off"
        disabled={disabled || unavailable}
        placeholder={unavailable ? (loading ? '正在读取类别…' : '类别目录不可用') : value === null ? '搜索名称、编码或别名' : '搜索以更换类别'}
        value={query}
        onChange={event => { setQuery(event.target.value); setActiveIndex(0); if (!open) setOpen(true) }}
        onFocus={show}
        onClick={show}
        onKeyDown={onKeyDown}
      />
      {loading && <LoaderCircle size={15} className="spin" aria-label="正在读取类别" />}
    </div>
    {open && <ul className="cp-list" id={listId} role="listbox" aria-label={`${label}候选`}>
      {shown.map(option => {
        const isActive = option.id === active?.id
        return <li
          key={option.id}
          id={`${listId}-${option.id}`}
          role="option"
          aria-selected={option.id === value}
          className={`cp-option${isActive ? ' is-active' : ''}${option.id === value ? ' is-selected' : ''}`}
          style={query.trim() ? undefined : { paddingLeft: `${10 + Math.min(option.depth, 6) * 14}px` }}
          onMouseDown={event => event.preventDefault()}
          onMouseMove={() => { const index = shown.indexOf(option); if (index !== activeIndex) setActiveIndex(index) }}
          onClick={() => pick(option.id)}
        >
          <span>{option.label}</span>
          <small>{query.trim() && option.path ? `${option.path} · ` : ''}<code>{option.code}</code></small>
        </li>
      })}
      {!shown.length && <li className="cp-empty" role="presentation">{options.some(option => option.isActive) ? '没有匹配的启用类别' : '还没有启用的类别，可在「类别配置」里新建'}</li>}
      {matches.length > shown.length && <li className="cp-more" role="presentation">还有 {matches.length - shown.length} 个，继续输入以缩小范围</li>}
    </ul>}
    {error
      ? <p className="cp-error" id={hintId} role="alert">{error}</p>
      : loadError
        ? <p className="cp-error" id={hintId} role="alert">{loadError}{onRetry && <> <button type="button" className="mg-text-button" onClick={onRetry}><RotateCcw size={13} />重试</button></>}</p>
        : hint ? <p className="cp-hint" id={hintId}>{hint}</p> : null}
  </div>
}
