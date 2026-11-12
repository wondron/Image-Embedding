import { useId, useState, type ReactNode } from 'react'
import { ChevronDown, CircleHelp, Hash, Search, SlidersHorizontal, X } from 'lucide-react'
import { LABEL_STATUS_TEXT, type LabelStatus } from '../../entities/image'
import type { CategoryTreeState } from '../category-picker'
import { SceneSelect } from '../scene-select'
import { CategoryNavigator } from './CategoryNavigator'
import { normalizeTag, type LibraryFilters } from './model'
import './library-filters.css'

const LABEL_CHOICES: ReadonlyArray<{ value: LabelStatus | null; title?: string }> = [
  { value: null },
  { value: 'unlabeled', title: '还没有类别的图片' },
  { value: 'auto', title: '有类别，还没有人工核验' },
  { value: 'verified', title: '类别经过人工核验' },
]

export interface LibraryFilterPanelProps {
  filters: LibraryFilters
  /** How many filters narrow the list now (the reset clears them). */
  activeCount: number
  onQueryChange: (query: string) => void
  onSceneChange: (scene: string) => void
  sceneDisabled?: boolean
  onLabelStatusChange: (status: LabelStatus | null) => void
  onOutOfSyncChange: (outOfSync: boolean) => void
  /** A normalized tag; '' clears the tag filter. */
  onTagChange: (tag: string) => void
  onCategoryChange: (id: number | null) => void
  onReset: () => void
  categories: CategoryTreeState
  /** Narrow screens show the filters only while open; wide screens always do. */
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The help behind the 「?」 of the header. */
  help?: ReactNode
}

/**
 * The library's tool column: every filter GET /api/images takes, compact rows on top and the
 * category tree below them, taking the rest of the column's height.
 */
export function LibraryFilterPanel({
  filters, activeCount, onQueryChange, onSceneChange, sceneDisabled = false, onLabelStatusChange,
  onOutOfSyncChange, onTagChange, onCategoryChange, onReset, categories, open, onOpenChange, help,
}: LibraryFilterPanelProps) {
  const id = useId()
  const [tagDraft, setTagDraft] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const appliedTag = normalizeTag(filters.tag)

  return <aside className={`lf-panel${open ? ' is-open' : ''}`} aria-label="图片筛选">
    <div className="lf-head">
      <SlidersHorizontal size={15} aria-hidden="true" />
      <h2>筛选</h2>
      {activeCount > 0 && <span className="lf-badge" title={`${activeCount} 项筛选生效`}>{activeCount}</span>}
      {help && <button type="button" className="lf-icon-button lf-help-toggle" aria-label="筛选说明" title="筛选说明" aria-expanded={helpOpen} aria-controls={`${id}-help`} onClick={() => setHelpOpen(current => !current)}><CircleHelp size={15} /></button>}
      <button type="button" className="mg-text-button lf-reset" disabled={activeCount === 0} onClick={onReset}>重置</button>
      <button type="button" className="lf-toggle" aria-expanded={open} aria-controls={`${id}-body`} onClick={() => onOpenChange(!open)}>
        {open ? '收起筛选' : '展开筛选'}<ChevronDown size={15} aria-hidden="true" />
      </button>
    </div>

    <div className="lf-body" id={`${id}-body`}>
      {help && helpOpen && <div className="lf-help" id={`${id}-help`}>{help}</div>}

      <div className="lf-filters">
        <div className="lf-input">
          <Search size={14} aria-hidden="true" />
          <input type="search" aria-label="关键词" placeholder="搜索：类别名、标签、链接或 ID" autoComplete="off" value={filters.query} onChange={event => onQueryChange(event.target.value)} />
          {filters.query && <button type="button" className="lf-clear" aria-label="清空关键词" onClick={() => onQueryChange('')}><X size={14} /></button>}
        </div>

        <SceneSelect id="library-scene" className="lf-row lf-scene" variant="management" compact value={filters.scene} disabled={sceneDisabled} onChange={onSceneChange} />

        <div className="lf-row">
          <span className="lf-row-label" aria-hidden="true">标注</span>
          <div className="lf-segment" role="radiogroup" aria-label="标注状态">
            {LABEL_CHOICES.map(choice => <label key={choice.value ?? 'all'} title={choice.title}>
              <input type="radio" name={`${id}-label`} checked={filters.labelStatus === choice.value} onChange={() => onLabelStatusChange(choice.value)} />
              <span>{choice.value ? LABEL_STATUS_TEXT[choice.value] : '全部'}</span>
            </label>)}
          </div>
        </div>

        <div className="lf-row">
          <span className="lf-row-label" aria-hidden="true" title="向量库状态">向量</span>
          <div className="lf-segment" role="radiogroup" aria-label="向量库状态">
            <label><input type="radio" name={`${id}-vector`} checked={!filters.outOfSync} onChange={() => onOutOfSyncChange(false)} /><span>全部</span></label>
            <label title="按写入记录：向量库里的过滤信息落后于数据库，或当前模型还没有这张图的向量">
              <input type="radio" name={`${id}-vector`} checked={filters.outOfSync} onChange={() => onOutOfSyncChange(true)} /><span>待同步或没有向量</span>
            </label>
          </div>
        </div>

        <div className="lf-row">
          <label className="lf-row-label" htmlFor={`${id}-tag`}>标签</label>
          <form className="lf-input" title="和标签完全一致才算；也可以点卡片上的标签" onSubmit={event => {
            event.preventDefault()
            const tag = normalizeTag(tagDraft)
            if (!tag) return
            onTagChange(tag)
            setTagDraft('')
          }}>
            <Hash size={14} aria-hidden="true" />
            <input id={`${id}-tag`} placeholder="完整标签，回车筛选" autoComplete="off" enterKeyHint="search" maxLength={70} value={tagDraft} onChange={event => setTagDraft(event.target.value)} />
          </form>
          {appliedTag && <span className="lf-applied-chip" title={appliedTag}>
            <span>#{appliedTag}</span>
            <button type="button" aria-label={`清除标签筛选：${appliedTag}`} onClick={() => onTagChange('')}><X size={13} /></button>
          </span>}
        </div>
      </div>

      <CategoryNavigator
        tree={categories.tree}
        loading={categories.loading}
        error={categories.error}
        onRetry={categories.refresh}
        value={filters.categoryId}
        onChange={onCategoryChange}
      />
    </div>
  </aside>
}
