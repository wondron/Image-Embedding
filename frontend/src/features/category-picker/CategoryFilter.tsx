import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, FolderTree, RotateCcw } from 'lucide-react'
import { categoryLabel, type CategoryTree } from '../../entities/category'
import { categoryPath } from './categoryOptions'
import { CategorySearch } from './CategorySearch'
import './category-picker.css'

/**
 * Toolbar filter by category: a button showing the choice that opens a searchable list of the
 * active categories (CategorySearch) with 「全部类别」 first. Picking a category means it and its
 * subtree (the list API's category_id).
 */
export function CategoryFilter({ tree, loading = false, loadError, onRetry, value, onChange, disabled = false }: {
  tree: CategoryTree | null
  loading?: boolean
  loadError?: string | null
  onRetry?: () => void
  value: number | null
  onChange: (id: number | null) => void
  disabled?: boolean
}) {
  const searchId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const node = value !== null ? tree?.byId.get(value)?.node ?? null : null
  const text = value === null ? '全部类别' : node ? categoryLabel(node) : `类别 #${value}`
  const path = value !== null && tree ? categoryPath(tree, value) : null

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  // Keyboard users tabbing out close it too
  return <div className="cf" ref={rootRef} onBlur={event => { if (open && !rootRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false) }}>
    <button
      ref={triggerRef}
      type="button"
      className={`mg-select cf-trigger${value !== null ? ' is-set' : ''}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={`类别筛选：${text}`}
      title={path ?? text}
      disabled={disabled}
      onClick={() => setOpen(current => !current)}
    >
      <FolderTree size={15} aria-hidden="true" />
      <span>{text}</span>
      <ChevronDown size={15} aria-hidden="true" />
    </button>
    {open && <div className="cf-popover" role="dialog" aria-label="按类别筛选">
      {loadError && <p className="cp-error" role="alert">{loadError}{onRetry && <> <button type="button" className="mg-text-button" onClick={onRetry}><RotateCcw size={13} />重试</button></>}</p>}
      <CategorySearch
        id={searchId}
        label="类别筛选"
        tree={tree}
        loading={loading}
        value={value}
        onPick={id => { onChange(id); close() }}
        alwaysOpen
        autoFocus
        nullOption="全部类别"
        onEscape={close}
        placeholder="搜索类别"
      />
      <p className="cf-hint">选中某个类别时，也包含它的所有下级类别。</p>
    </div>}
  </div>
}
