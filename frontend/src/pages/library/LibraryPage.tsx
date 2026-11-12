import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { ArrowRight, Grid2x2, Grid3x3, ImagePlus, Images, Layers3, List, LoaderCircle, PencilLine, Plus, Trash2, X, type LucideIcon } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { listImages, type ImageDeleteResult, type LabelStatus, type LibraryImageItem } from '../../entities/image'
import { sceneDisplayLabel, toApiSceneCode } from '../../entities/scene'
import { EMPTY_COPY } from '../../shared/config'
import { oneOf, usePreference } from '../../shared/lib'
import { EmptyState, notify } from '../../shared/ui'
import { categoryPath, type CategoryTreeState } from '../../features/category-picker'
import { ImageEditPanel } from '../../features/image-edit'
import {
  LIBRARY_PAGE_SIZES,
  LibraryFilterPanel,
  activeFilters,
  clearAllFilters,
  clearFilter,
  normalizeTag,
  pageCount,
  pageRange,
  withCategory,
  withLabelStatus,
  type FilterKey,
  type FilterLabels,
  type LibraryFilters,
  type LibraryPageSize,
} from '../../features/library-filters'
import { LIBRARY_LAYOUTS, LibraryPagination, LibraryResults, useLibraryList, type LibraryLayout } from '../../features/library-list'
import { useSceneSelection } from '../../features/scene-catalog'
import { BatchDeleteDialog, BatchEditDialog, useLibrarySelection, type BatchOutcome } from '../../features/library-batch'
import './library-page.css'

/** 「选择全部筛选结果」 reads them page by page; beyond this many the person narrows the filters first. */
const SELECT_ALL_LIMIT = 1000
const SELECT_ALL_PAGE = 100

const LAYOUT_BUTTONS: ReadonlyArray<{ layout: LibraryLayout; label: string; icon: LucideIcon }> = [
  { layout: 'large', label: '大图展示', icon: Grid2x2 },
  { layout: 'small', label: '小图展示', icon: Grid3x3 },
  { layout: 'list', label: '列表展示', icon: List },
]

export interface LibraryPageProps {
  apiBase: string
  /** Owned by the management host, so they survive a visit to 上传与导入 or 入库任务. */
  filters: LibraryFilters
  setFilters: Dispatch<SetStateAction<LibraryFilters>>
  page: number
  setPage: Dispatch<SetStateAction<number>>
  /** The category tree, shared with the import page (read again whenever either opens). */
  categories: CategoryTreeState
  /** Images of this session's upload queue that are not stored yet. */
  pendingImports: number
  onImport: () => void
  /** Scene management, on its sync tab. */
  onOpenSceneSync: () => void
}

/**
 * 图片数据: the filters on the left (GET /api/images takes each of them), the images on the right,
 * paged by the server. An image opens in the editor (ImageEditPanel).
 */
export function LibraryPage({ apiBase, filters, setFilters, page, setPage, categories, pendingImports, onImport, onOpenSceneSync }: LibraryPageProps) {
  const [layout, setLayout] = usePreference<LibraryLayout>('food-atlas-library-layout', 'large', oneOf(LIBRARY_LAYOUTS))
  const [pageSize, setPageSize] = usePreference<LibraryPageSize>('food-atlas-library-page-size', 24, oneOf(LIBRARY_PAGE_SIZES))
  /** The keyword as last requested: typing waits for a pause. */
  const [requestedQuery, setRequestedQuery] = useState(() => filters.query.trim())
  const [reloadKey, setReloadKey] = useState(0)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [preview, setPreview] = useState<LibraryImageItem | null>(null)
  /** The open editor has unsaved edits: its dialog stays open until they are saved or undone. */
  const [dirty, setDirty] = useState(false)
  /** The open editor wrote something: category counts may have moved. */
  const wrote = useRef(false)
  const content = useRef<HTMLDivElement>(null)
  /** The open batch dialog, with the images it was opened for (they do not change while it runs). */
  const [batch, setBatch] = useState<{ kind: 'edit' | 'delete'; items: LibraryImageItem[] } | null>(null)
  const [selectingAll, setSelectingAll] = useState(false)
  const selectAllRun = useRef<AbortController | null>(null)

  const handleScene = useCallback((code: string) => {
    const scene = toApiSceneCode(code)
    setFilters(current => (current.scene === scene ? current : { ...current, scene }))
    setPage(1)
  }, [setFilters, setPage])
  const scenes = useSceneSelection(filters.scene, handleScene, apiBase)

  useEffect(() => {
    const next = filters.query.trim()
    if (next === requestedQuery) return
    const timer = window.setTimeout(() => { setRequestedQuery(next); setPage(1) }, 300)
    return () => window.clearTimeout(timer)
  }, [filters.query, requestedQuery, setPage])

  const tag = normalizeTag(filters.tag)
  const list = useLibraryList({
    enabled: scenes.selectionValid,
    apiBase,
    page,
    pageSize,
    q: requestedQuery || undefined,
    categoryId: filters.categoryId,
    labelStatus: filters.labelStatus,
    outOfSync: filters.outOfSync,
    tag: tag || undefined,
    scene: filters.scene,
    reloadKey,
  })
  const items = list.items
  const totalPages = pageCount(list.total, pageSize)
  /** The result set the picks belong to: other filters drop them. */
  const filterKey = JSON.stringify([apiBase, requestedQuery, filters.categoryId, filters.labelStatus, filters.outOfSync, tag, filters.scene])
  const selection = useLibrarySelection(items, filterKey)
  // Reading every result for 「选择全部」 stops when the filters change
  useEffect(() => () => selectAllRun.current?.abort(), [filterKey])
  useEffect(() => {
    // The list shrank under the page (deletes elsewhere): go to the last page there is
    if (list.total > 0 && items.length === 0 && page > totalPages) setPage(totalPages)
  }, [list.total, items.length, page, totalPages, setPage])
  // Another page or other filters start at the top of the image area
  useEffect(() => { content.current?.scrollTo?.({ top: 0 }) }, [page, pageSize, requestedQuery, filters.categoryId, filters.labelStatus, filters.outOfSync, tag, filters.scene])

  const tree = categories.tree
  const pathOf = useCallback((id: number) => (tree ? categoryPath(tree, id) : null), [tree])
  const sceneName = useCallback(
    (code: string) => scenes.data?.scenes.find(scene => scene.code === code)?.name ?? sceneDisplayLabel(code),
    [scenes.data],
  )
  const labels: FilterLabels = { category: id => pathOf(id) ?? `类别 #${id}`, scene: sceneName, defaultScene: scenes.defaultScene?.code ?? null }
  const chips = activeFilters(filters, labels)
  /** What the shown list was asked for (the keyword may still be waiting for a pause). */
  const filtered = activeFilters({ ...filters, query: requestedQuery }, labels).length > 0

  function apply(next: LibraryFilters, message = '') {
    setFilters(next)
    setPage(1)
    if (message) notify.info(message, { title: '筛选已调整' })
  }

  function changeQuery(text: string) {
    setFilters(current => ({ ...current, query: text }))
    // Clearing the box applies at once; typing waits for a pause
    if (!text.trim() && requestedQuery) { setRequestedQuery(''); setPage(1) }
  }

  function changeCategory(id: number | null) {
    if (id === filters.categoryId) return
    const change = withCategory(filters, id)
    apply(change.filters, change.dropped === 'labelStatus' ? '已取消「未标注」：未标注的图片没有类别，和类别筛选不会同时满足。' : '')
  }

  function changeLabelStatus(status: LabelStatus | null) {
    const change = withLabelStatus(filters, status)
    apply(change.filters, change.dropped === 'category' ? '已清除类别筛选：「未标注」的图片没有类别。' : '')
  }

  function changeTag(next: string) {
    const normalized = normalizeTag(next)
    if (normalized !== tag) apply({ ...filters, tag: normalized })
  }

  function removeFilter(key: FilterKey) {
    apply(clearFilter(filters, key, labels.defaultScene))
    if (key === 'query') setRequestedQuery('')
  }

  function resetFilters() {
    apply(clearAllFilters(filters, labels.defaultScene))
    setRequestedQuery('')
  }

  function openImage(item: LibraryImageItem) {
    wrote.current = false
    setDirty(false)
    setPreview(item)
  }

  function closeImage() {
    setPreview(null)
    setDirty(false)
    if (wrote.current) categories.refresh()
    wrote.current = false
  }

  const reload = useCallback(() => {
    wrote.current = true
    setReloadKey(key => key + 1)
  }, [])

  const refreshCategories = categories.refresh
  const imageDeleted = useCallback((result: ImageDeleteResult | null) => {
    setPreview(null)
    setDirty(false)
    wrote.current = false
    refreshCategories()
    if (result === null) notify.info('这张图片已经被删除了。')
    else notify.success(result.cleanup === 'done' ? '已删除图片，向量和原图、缩略图已清理。' : '已删除图片；向量和图片文件稍后由清理任务删除。')
  }, [refreshCategories])

  /** Pick every image the filters match, read page by page (up to SELECT_ALL_LIMIT). */
  async function selectAllResults() {
    if (list.total > SELECT_ALL_LIMIT) {
      notify.warn(`当前筛选结果有 ${list.total.toLocaleString('zh-CN')} 张，一次最多选择 ${SELECT_ALL_LIMIT} 张。请先缩小筛选范围，或分几批处理。`, { title: '图片太多' })
      return
    }
    selectAllRun.current?.abort()
    const controller = new AbortController()
    selectAllRun.current = controller
    setSelectingAll(true)
    try {
      const found: LibraryImageItem[] = []
      for (let next = 1; next <= Math.ceil(SELECT_ALL_LIMIT / SELECT_ALL_PAGE); next += 1) {
        const result = await listImages({
          page: next, pageSize: SELECT_ALL_PAGE, q: requestedQuery || undefined, categoryId: filters.categoryId,
          labelStatus: filters.labelStatus, outOfSync: filters.outOfSync, tag: tag || undefined, scene: filters.scene,
          baseUrl: apiBase, signal: controller.signal,
        })
        found.push(...result.items)
        if (result.items.length < SELECT_ALL_PAGE || found.length >= result.total) break
      }
      if (!controller.signal.aborted) selection.add(found)
    } catch (error) {
      if (!controller.signal.aborted) notify.error(error instanceof Error ? error.message : '读取筛选结果失败，请重试。', { title: '没能选择全部' })
    } finally {
      if (selectAllRun.current === controller) {
        selectAllRun.current = null
        setSelectingAll(false)
      }
    }
  }

  /** After a batch run: the list and the category counts read again; deleted images leave the picks. */
  function batchFinished(kind: 'edit' | 'delete', outcomes: ReadonlyMap<string, BatchOutcome>) {
    setReloadKey(key => key + 1)
    categories.refresh()
    if (kind === 'delete') selection.remove([...outcomes].filter(([, outcome]) => outcome.status === 'done').map(([id]) => id))
  }

  const vectorUnreadable = items.length > 0 && items.every(item => item.vectorStatus === 'unavailable')
  const staleOnPage = items.filter(item => item.vectorStatus === 'stale').length
  const missingOnPage = items.filter(item => item.vectorStatus === 'missing').length
  const vectorTally = [staleOnPage ? `${staleOnPage} 张待同步` : '', missingOnPage ? `${missingOnPage} 张没有向量` : ''].filter(Boolean).join('、')
  const status = list.loading ? '图库加载中…' : list.error ? '图库加载失败' : list.connected ? (vectorUnreadable ? '图库已接入 · 向量库状态读不到' : '图库已接入') : '图库未连接'
  const firstLoad = list.loading && items.length === 0
  const waitingForScene = !scenes.selectionValid && !list.error
  const range = pageRange(page, pageSize, list.total)

  return <div className="lib-layout">
    <LibraryFilterPanel
      filters={filters}
      activeCount={chips.length}
      onQueryChange={changeQuery}
      onSceneChange={handleScene}
      sceneDisabled={list.loading}
      onLabelStatusChange={changeLabelStatus}
      onOutOfSyncChange={outOfSync => apply({ ...filters, outOfSync })}
      onTagChange={changeTag}
      onCategoryChange={changeCategory}
      onReset={resetFilters}
      categories={categories}
      open={toolsOpen}
      onOpenChange={setToolsOpen}
      help={<>
        <p>按类别筛选时包含它的所有下级类别；类别旁的数字是图片数（含下级，不分场景）。点卡片上的类别或标签，可以直接按它筛选。</p>
        <p>关键词匹配类别名、标签、图片链接和 ID；标签要和图片上的标签完全一致。「未标注」就是没有类别的图片，所以不和类别同时生效。</p>
        <p>点击图片查看与标注：可改类别、标注状态、标签、描述和食物标注，保存后自动同步到向量库；也可以删除图片。标着「待同步」的图片，打开后可一键同步（批量同步在「场景管理」）；「没有向量」的检索查不到，打开后可用存储里的原图重新生成。</p>
      </>}
    />

    <section className="lib-main" aria-labelledby="library-results-title">
      <header className="lib-head">
        <div className="lib-head-title">
          <h2 id="library-results-title">图片</h2>
          <span className="lib-count">{list.error ? '—' : firstLoad || waitingForScene ? '…' : `${list.total.toLocaleString('zh-CN')} 张`}</span>
          {list.loading && !firstLoad && <LoaderCircle size={14} className="spin lib-busy" aria-label="正在更新" />}
          <span className={`mg-status lib-status${list.error ? ' is-error' : ''}`}><span />{status}</span>
        </div>
        <div className="lib-head-tools">
          <label className="lib-page-size">
            <span>每页</span>
            <select className="mg-select" aria-label="每页数量" value={pageSize} onChange={event => { setPageSize(Number(event.target.value) as LibraryPageSize); setPage(1) }}>
              {LIBRARY_PAGE_SIZES.map(size => <option key={size} value={size}>{size} 张</option>)}
            </select>
          </label>
          <div className="mg-view-toggle" role="group" aria-label="图片展示方式">
            {LAYOUT_BUTTONS.map(({ layout: value, label, icon: Icon }) => (
              <button type="button" key={value} className={layout === value ? 'active' : ''} aria-label={label} title={label} aria-pressed={layout === value} onClick={() => setLayout(value)}><Icon size={16} /></button>
            ))}
          </div>
          {pendingImports > 0 && <button type="button" className="mg-text-button lib-pending" title="本会话上传队列里还没有入库的图片" onClick={onImport}>{pendingImports} 张待入库<ArrowRight size={13} /></button>}
          <button type="button" className="mg-btn mg-primary" onClick={onImport}><Plus size={17} />导入图片</button>
        </div>
      </header>

      {chips.length > 0 && <div className="lib-chips" role="group" aria-label="生效的筛选">
        {chips.map(chip => <span className="lib-chip" key={chip.key}>
          <small>{chip.name}</small>
          <span className="lib-chip-value" title={chip.value}>{chip.value}</span>
          <button type="button" aria-label={`清除${chip.name}筛选`} title={`清除${chip.name}筛选`} onClick={() => removeFilter(chip.key)}><X size={13} /></button>
        </span>)}
        <button type="button" className="mg-text-button" onClick={resetFilters}>清空筛选</button>
      </div>}

      {!list.error && items.length > 0 && <div className="lib-selection" role="toolbar" aria-label="批量操作">
        <label className="lib-select-page">
          <input
            type="checkbox"
            aria-label="选择本页"
            checked={selection.pageAll}
            ref={element => { if (element) element.indeterminate = selection.pageSome && !selection.pageAll }}
            onChange={() => selection.setPage(!selection.pageAll)}
          />
          本页
        </label>
        {list.total > items.length && <button type="button" className="mg-text-button" disabled={selectingAll} onClick={() => void selectAllResults()}>
          {selectingAll ? <><LoaderCircle size={13} className="spin" />正在选择…</> : `选择全部 ${list.total.toLocaleString('zh-CN')} 张`}
        </button>}
        <span className="lib-selected-count" role="status">{selection.selected.size ? `已选 ${selection.selected.size.toLocaleString('zh-CN')} 张` : '可勾选图片后批量操作'}</span>
        {selection.selected.size > 0 && <button type="button" className="mg-text-button" onClick={selection.clear}>清空选择</button>}
        <span className="lib-selection-actions">
          <button type="button" className="mg-btn mg-secondary" disabled={!selection.selected.size} onClick={() => setBatch({ kind: 'edit', items: [...selection.selected.values()] })}><PencilLine size={15} />批量修改</button>
          <button type="button" className="mg-btn lb-danger" disabled={!selection.selected.size} onClick={() => setBatch({ kind: 'delete', items: [...selection.selected.values()] })}><Trash2 size={15} />批量删除</button>
        </span>
      </div>}

      <div className={`lib-content${layout === 'list' ? ' is-list' : ''}`} ref={content}>
        {list.error && <div className="mg-errors" role="alert"><div><strong>图库加载失败</strong><button type="button" className="mg-text-button" onClick={() => setReloadKey(key => key + 1)}>重试</button></div><p>{list.error}</p></div>}
        {waitingForScene && !scenes.loading && (scenes.error || scenes.configurationError)
          ? <EmptyState icon={Layers3} title="场景目录不可用" description={scenes.error ?? '当前默认场景不可用，图库暂时不能加载。'}>
            <button type="button" className="mg-btn mg-secondary" onClick={() => void scenes.refresh()}>重新读取场景</button>
          </EmptyState>
          : (firstLoad || waitingForScene) && !list.error
            ? <div className="lib-loading" aria-busy="true">
              <p role="status"><LoaderCircle size={15} className="spin" />正在加载图库…</p>
              <div className={`lib-grid is-${layout === 'list' ? 'small' : layout} lib-skeleton`} aria-hidden="true">{Array.from({ length: 8 }, (_, index) => <span key={index} />)}</div>
            </div>
            : !list.error && !list.loading && list.total === 0
              ? filtered
                ? <EmptyState icon={Images} title={EMPTY_COPY.libraryFilteredEmpty.title} description={EMPTY_COPY.libraryFilteredEmpty.description}>
                  <button type="button" className="mg-btn mg-secondary" onClick={resetFilters}>清空筛选</button>
                </EmptyState>
                : <EmptyState icon={Images} title={EMPTY_COPY.libraryEmpty.title} description={EMPTY_COPY.libraryEmpty.description}>
                  <button type="button" className="mg-btn mg-secondary" onClick={onImport}><ImagePlus size={17} />准备第一批图片<ArrowRight size={16} /></button>
                </EmptyState>
              : !list.error && items.length > 0 && <LibraryResults
                items={items}
                layout={layout}
                loading={list.loading}
                categoryPath={pathOf}
                sceneName={sceneName}
                activeCategoryId={filters.categoryId}
                activeTag={tag}
                onOpen={openImage}
                onCategory={changeCategory}
                onTag={changeTag}
                selected={selection.ids}
                onToggle={selection.toggle}
              />}
      </div>

      <LibraryPagination
        page={page}
        pageCount={totalPages}
        disabled={list.loading || list.total === 0}
        onPage={setPage}
        summary={<>
          {list.error ? '加载失败' : firstLoad ? '加载中…' : list.total === 0 ? '暂无图片' : `共 ${list.total.toLocaleString('zh-CN')} 张${range ? ` · 第 ${range} 张` : ''}`}
          {!list.error && vectorTally && <span className="mg-vector-tally"> · 本页 {vectorTally}</span>}
        </>}
      />
    </section>

    {preview && <Dialog title="图片标注" size="lg" onClose={closeImage} dismissDisabled={dirty}>
      <ImageEditPanel
        item={preview}
        apiBase={apiBase}
        categories={categories}
        onChanged={reload}
        onDeleted={imageDeleted}
        onDirtyChange={setDirty}
        onManage={() => { closeImage(); onOpenSceneSync() }}
        onUpload={dirty ? undefined : () => { closeImage(); onImport() }}
      />
    </Dialog>}

    {batch?.kind === 'edit' && <BatchEditDialog
      items={batch.items}
      apiBase={apiBase}
      categories={categories}
      onClose={() => setBatch(null)}
      onFinished={outcomes => batchFinished('edit', outcomes)}
    />}
    {batch?.kind === 'delete' && <BatchDeleteDialog
      items={batch.items}
      apiBase={apiBase}
      onClose={() => setBatch(null)}
      onFinished={outcomes => batchFinished('delete', outcomes)}
    />}
  </div>
}
