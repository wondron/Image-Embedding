import { Images } from 'lucide-react'
import { LABEL_STATUS_TEXT, StoredImage, type LibraryImageItem, type VectorStatus } from '../../entities/image'
import { formatLibraryTime } from '../../shared/lib'

export type LibraryLayout = 'large' | 'small' | 'list'
export const LIBRARY_LAYOUTS: readonly LibraryLayout[] = ['large', 'small', 'list']

/** Only what needs attention gets a flag: an image in step with the vector store stays quiet. */
const VECTOR_FLAG: Partial<Record<VectorStatus, { text: string; title: string }>> = {
  stale: { text: '待同步', title: '向量库里的过滤信息落后于数据库，检索可能按旧标注命中它；打开图片可以同步' },
  missing: { text: '没有向量', title: '向量库里没有这张图，检索查不到它；打开图片可以用原图重新生成' },
}

function VectorFlag({ status }: { status: VectorStatus | null }) {
  const flag = status ? VECTOR_FLAG[status] : undefined
  return flag ? <span className={`mg-vector-flag is-${status}`} title={flag.title}>{flag.text}</span> : null
}

/** A card's title: the start of its description, else a short id. */
export function libraryTitle(item: LibraryImageItem): string {
  const text = Array.from(item.description.trim())
  if (text.length) return text.length > 24 ? `${text.slice(0, 24).join('')}…` : text.join('')
  return `#${item.id.slice(0, 8)}`
}

export interface LibraryResultsProps {
  items: LibraryImageItem[]
  layout: LibraryLayout
  /** A newer page is on its way: the shown one is dimmed and not clickable. */
  loading: boolean
  /** 「菜品 / 肉类 / 牛排」, or null when the tree does not have the category. */
  categoryPath: (id: number) => string | null
  sceneName: (code: string) => string
  /** The filters now, so the chip of the current one is not offered again. */
  activeCategoryId: number | null
  activeTag: string
  onOpen: (item: LibraryImageItem) => void
  onCategory: (id: number) => void
  onTag: (tag: string) => void
  /** Ids of the images picked for a batch action. */
  selected: ReadonlySet<string>
  /** Pick or unpick one; range: with Shift, everything from the last one picked on this page. */
  onToggle: (item: LibraryImageItem, range: boolean) => void
}

/** The checkbox of one image (Space and Shift+click work too). */
function SelectBox({ item, title, selected, onToggle, className }: { item: LibraryImageItem; title: string; selected: boolean; onToggle: LibraryResultsProps['onToggle']; className: string }) {
  return <label className={className} title={selected ? '取消选择' : '选择（按住 Shift 可连选）'}>
    <input type="checkbox" aria-label={`选择 ${title}`} checked={selected} onChange={() => undefined} onClick={event => onToggle(item, event.shiftKey)} />
  </label>
}

type ChipProps = Pick<LibraryResultsProps, 'categoryPath' | 'activeCategoryId' | 'onCategory'> & { item: LibraryImageItem }

/** The image's category; a click filters by it (with its subtree). */
function CategoryChip({ item, categoryPath, activeCategoryId, onCategory }: ChipProps) {
  if (item.categoryId === null) return <span className="mg-category-chip is-empty">未标注</span>
  const categoryId = item.categoryId
  const name = item.category ?? `类别 #${categoryId}`
  const path = categoryPath(categoryId) ?? name
  if (categoryId === activeCategoryId) return <span className="mg-category-chip is-current" title={`${path}（当前筛选）`}>{name}</span>
  return <button type="button" className="mg-category-chip lib-filter-chip" title={`只看这个类别：${path}`} aria-label={`按类别筛选：${name}`} onClick={() => onCategory(categoryId)}>{name}</button>
}

function TagChips({ tags, limit, activeTag, onTag }: { tags: string[]; limit: number; activeTag: string; onTag: (tag: string) => void }) {
  return <>
    {tags.slice(0, limit).map(tag => (tag === activeTag
      ? <span className="lib-tag is-current" key={tag} title="当前筛选的标签">#{tag}</span>
      : <button type="button" className="lib-tag" key={tag} title="只看带这个标签的图片" aria-label={`按标签筛选：${tag}`} onClick={() => onTag(tag)}>#{tag}</button>))}
    {tags.length > limit && <span className="lib-tag-more" title={tags.slice(limit).map(tag => `#${tag}`).join(' ')}>+{tags.length - limit}</span>}
  </>
}

/** One library page as cards (large or small) or as rows. */
export function LibraryResults({ items, layout, loading, categoryPath, sceneName, activeCategoryId, activeTag, onOpen, onCategory, onTag, selected, onToggle }: LibraryResultsProps) {
  const chip = { categoryPath, activeCategoryId, onCategory }
  if (layout === 'list') {
    return <div className={`mg-library-list${loading ? ' is-loading' : ''}`} aria-busy={loading}>
      <div className="mg-table-head mg-library-head" aria-hidden="true"><span /><span>图片 / 名称</span><span>类别</span><span>标注 · 标签</span><span>入库时间</span></div>
      <div role="list">
        {items.map(item => {
          const title = libraryTitle(item)
          const path = item.categoryId !== null ? categoryPath(item.categoryId) : null
          const parents = path?.includes(' / ') ? path.slice(0, path.lastIndexOf(' / ')) : null
          return <div className={`mg-library-row mg-table-row${selected.has(item.id) ? ' is-selected' : ''}`} role="listitem" key={item.id}>
            <SelectBox item={item} title={title} selected={selected.has(item.id)} onToggle={onToggle} className="lib-row-check" />
            <button type="button" className="mg-library-name" onClick={() => onOpen(item)} aria-label={`标注 ${title}`}>
              <StoredImage
                id={item.id}
                url={item.imageUrl}
                className="mg-library-thumb"
                placeholder={<span className="mg-library-thumb mg-library-placeholder" aria-hidden="true"><Images size={16} /></span>}
              />
              <strong title={item.description || item.id}>{title}</strong>
            </button>
            <span className="lib-row-category"><CategoryChip item={item} {...chip} />{parents && <small title={path ?? undefined}>{parents}</small>}</span>
            <span className="mg-attributes">
              {item.labelStatus && item.labelStatus !== 'unlabeled' && <span className={`mg-label-status is-${item.labelStatus}`}>{LABEL_STATUS_TEXT[item.labelStatus]}</span>}
              <VectorFlag status={item.vectorStatus} />
              {item.tags.length > 0
                ? <TagChips tags={item.tags} limit={6} activeTag={activeTag} onTag={onTag} />
                : item.labelStatus === 'unlabeled' ? null : <span className="lib-none">—</span>}
            </span>
            <span className="lib-row-time">{formatLibraryTime(item.createdAt)}</span>
          </div>
        })}
      </div>
    </div>
  }
  return <div className={`mg-library-grid lib-grid is-${layout}${loading ? ' is-loading' : ''}`} aria-busy={loading}>
    {items.map(item => {
      const title = libraryTitle(item)
      return <article className={`mg-upload-card mg-library-card${selected.has(item.id) ? ' is-selected' : ''}`} key={item.id}>
        <SelectBox item={item} title={title} selected={selected.has(item.id)} onToggle={onToggle} className="lib-card-check" />
        <button type="button" className="mg-upload-preview" onClick={() => onOpen(item)} aria-label={`标注 ${title}`}>
          <StoredImage
            id={item.id}
            url={item.imageUrl}
            placeholder={<span className="mg-library-placeholder" aria-hidden="true"><Images size={28} strokeWidth={1.4} /></span>}
          />
          <span>查看与标注</span>
        </button>
        <div>
          <strong title={item.description || item.id}>{title}</strong>
          <small>
            <CategoryChip item={item} {...chip} />
            {item.labelStatus && item.labelStatus !== 'unlabeled' && <span className={`mg-label-status is-${item.labelStatus}`}>{LABEL_STATUS_TEXT[item.labelStatus]}</span>}
            <VectorFlag status={item.vectorStatus} />
            {layout === 'large' && item.scenes.map(code => <span className="mg-library-scene" key={code}>{sceneName(code)}</span>)}
          </small>
          {layout === 'large' && item.tags.length > 0 && <small className="mg-library-tags"><TagChips tags={item.tags} limit={4} activeTag={activeTag} onTag={onTag} /></small>}
        </div>
      </article>
    })}
  </div>
}
