import { useId } from 'react'
import { RotateCcw, X } from 'lucide-react'
import type { CategoryTree } from '../../entities/category'
import { categoryPath } from './categoryOptions'
import { CategorySearch } from './CategorySearch'
import './category-picker.css'

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
 * Category form field over GET /api/categories: the chosen category (name, path, code, clear)
 * above a search (CategorySearch) whose list opens in place, not as an overlay, so it works
 * inside panels that clip and inside dialogs.
 */
export function CategoryPicker({
  id, label, tree, loading = false, loadError, onRetry, value, onChange, disabled = false, error, hint,
}: CategoryPickerProps) {
  const hintId = useId()
  const selected = value !== null && tree ? tree.byId.get(value)?.node ?? null : null
  const selectedName = selected ? selected.name ?? selected.code : null
  const selectedPath = value !== null && tree ? categoryPath(tree, value) : null

  function clear() {
    onChange(null)
    document.getElementById(id)?.focus()
  }

  return <div className={`cp${error ? ' is-invalid' : ''}`}>
    <label className="cp-label" htmlFor={id}>{label}</label>
    {value !== null && <div className="cp-selected">
      <div>
        <strong>{selectedName ?? `类别 #${value}`}</strong>
        {selectedPath && selectedPath !== selectedName && <small>{selectedPath}</small>}
        {selected && <code>{selected.code}</code>}
        {selected && !selected.isActive && <small className="cp-inactive">已停用</small>}
      </div>
      <button type="button" className="cp-clear" disabled={disabled} aria-label={`清除类别 ${selectedName ?? ''}`.trim()} onClick={clear}><X size={14} /></button>
    </div>}
    <CategorySearch
      id={id}
      label={label}
      tree={tree}
      loading={loading}
      value={value}
      onPick={onChange}
      disabled={disabled}
      placeholder={value === null ? '搜索名称、编码或别名' : '搜索以更换类别'}
      describedBy={error || loadError || hint ? hintId : undefined}
      invalid={Boolean(error)}
    />
    {error
      ? <p className="cp-error" id={hintId} role="alert">{error}</p>
      : loadError
        ? <p className="cp-error" id={hintId} role="alert">{loadError}{onRetry && <> <button type="button" className="mg-text-button" onClick={onRetry}><RotateCcw size={13} />重试</button></>}</p>
        : hint ? <p className="cp-hint" id={hintId}>{hint}</p> : null}
  </div>
}
