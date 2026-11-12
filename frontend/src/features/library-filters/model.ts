import { LABEL_STATUS_TEXT, type LabelStatus } from '../../entities/image'
import { DEFAULT_SCENE } from '../../entities/scene'

/** What the library list is narrowed by; GET /api/images takes each of them. */
export interface LibraryFilters {
  /** The search box as typed; the request sends it trimmed, after a pause. */
  query: string
  /** That category and every category below it; null: every category. */
  categoryId: number | null
  /** null: every status. 「未标注」 means no category, so it never goes together with a category. */
  labelStatus: LabelStatus | null
  /** Only images whose vector store side needs a push or a vector. */
  outOfSync: boolean
  /** One tag, matched exactly; '' none. */
  tag: string
  /** The scene whose members are listed. */
  scene: string
}

export const INITIAL_LIBRARY_FILTERS: Readonly<LibraryFilters> = {
  query: '', categoryId: null, labelStatus: null, outOfSync: false, tag: '', scene: DEFAULT_SCENE,
}

export const LIBRARY_PAGE_SIZES = [24, 48, 96] as const
export type LibraryPageSize = (typeof LIBRARY_PAGE_SIZES)[number]

/** The filter a change also cleared, so the page can say why it went away. */
export type DroppedFilter = 'category' | 'labelStatus' | null

export interface FilterChange {
  filters: LibraryFilters
  dropped: DroppedFilter
}

/** Pick a category (null: all). 「未标注」 goes away with it: an image without a category never matches one. */
export function withCategory(filters: LibraryFilters, categoryId: number | null): FilterChange {
  if (categoryId !== null && filters.labelStatus === 'unlabeled') {
    return { filters: { ...filters, categoryId, labelStatus: null }, dropped: 'labelStatus' }
  }
  return { filters: { ...filters, categoryId }, dropped: null }
}

/** Pick a label status (null: all). 「未标注」 drops the category for the same reason. */
export function withLabelStatus(filters: LibraryFilters, labelStatus: LabelStatus | null): FilterChange {
  if (labelStatus === 'unlabeled' && filters.categoryId !== null) {
    return { filters: { ...filters, labelStatus, categoryId: null }, dropped: 'category' }
  }
  return { filters: { ...filters, labelStatus }, dropped: null }
}

/**
 * What a typed tag filters by: trimmed, without the one 「#」 the cards put in front of a tag
 * (a stored tag is matched exactly and never starts or ends with whitespace).
 */
export function normalizeTag(text: string): string {
  const trimmed = text.trim()
  return (trimmed.startsWith('#') ? trimmed.slice(1) : trimmed).trim()
}

export type FilterKey = 'query' | 'scene' | 'labelStatus' | 'outOfSync' | 'tag' | 'category'

export interface ActiveFilter {
  key: FilterKey
  /** 「类别」 */
  name: string
  /** 「菜品 / 肉类」 */
  value: string
}

export interface FilterLabels {
  /** The category as the page names it, e.g. its path. */
  category: (id: number) => string
  scene: (code: string) => string
  /** The scene the page opens on: being in it is not a filter. null while the catalog is unknown. */
  defaultScene: string | null
}

/** The filters that narrow the list, in the order of the panel; the default scene is not one. */
export function activeFilters(filters: LibraryFilters, labels: FilterLabels): ActiveFilter[] {
  const out: ActiveFilter[] = []
  const query = filters.query.trim()
  if (query) out.push({ key: 'query', name: '关键词', value: query })
  if (labels.defaultScene !== null && filters.scene !== labels.defaultScene) out.push({ key: 'scene', name: '场景', value: labels.scene(filters.scene) })
  if (filters.labelStatus) out.push({ key: 'labelStatus', name: '标注', value: LABEL_STATUS_TEXT[filters.labelStatus] })
  if (filters.outOfSync) out.push({ key: 'outOfSync', name: '向量', value: '待同步或没有向量' })
  const tag = normalizeTag(filters.tag)
  if (tag) out.push({ key: 'tag', name: '标签', value: tag })
  if (filters.categoryId !== null) out.push({ key: 'category', name: '类别', value: labels.category(filters.categoryId) })
  return out
}

/** The filters without one of them; the scene goes back to the default one. */
export function clearFilter(filters: LibraryFilters, key: FilterKey, defaultScene: string | null): LibraryFilters {
  switch (key) {
    case 'query': return { ...filters, query: '' }
    case 'scene': return { ...filters, scene: defaultScene ?? filters.scene }
    case 'labelStatus': return { ...filters, labelStatus: null }
    case 'outOfSync': return { ...filters, outOfSync: false }
    case 'tag': return { ...filters, tag: '' }
    case 'category': return { ...filters, categoryId: null }
  }
}

/** Every filter off, in the default scene (kept as it is while the catalog is unknown). */
export function clearAllFilters(filters: LibraryFilters, defaultScene: string | null): LibraryFilters {
  return { ...INITIAL_LIBRARY_FILTERS, scene: defaultScene ?? filters.scene }
}

/** 1-based page → the range it shows, e.g. 「25–48」; '' for an empty list. */
export function pageRange(page: number, pageSize: number, total: number): string {
  if (total <= 0) return ''
  const first = (page - 1) * pageSize + 1
  if (first > total) return ''
  return `${first}–${Math.min(page * pageSize, total)}`
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize) || 1)
}
