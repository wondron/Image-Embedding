import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, FolderTree, LoaderCircle, Plus, RotateCcw, Search, X } from 'lucide-react'
import {
  buildCategoryTree,
  categoryLabel,
  createCategory,
  deleteCategory,
  listCategories,
  toCategoryFailure,
  updateCategory,
  type CategoryFailureView,
  type CategoryNode,
  type CategoryTreeNode,
  type HeatingGroupRef,
} from '../../entities/category'
import { enumValueLabel, useEnumCatalog } from '../../entities/enum'
import { requestSignal } from '../../shared/lib'
import { EmptyState } from '../../shared/ui'
import { CategoryDeleteDialog } from './CategoryDeleteDialog'
import { CategoryEditorDialog, type CategoryEditorTarget, type CategorySaveRequest } from './CategoryEditorDialog'
import { CategoryTable, rowElementId } from './CategoryTable'
import { NO_CATEGORY_FILTER, categoryTableView, type CategoryStatusFilter, type CategoryTableFilter } from './tableModel'
import { deleteFailureText, loadFailureText, saveFailureText } from './messages'
import '../../components/ManagementViews.css'
import './category-config.css'

const REQUEST_TIMEOUT_MS = 20_000
/** The system enum whose labels name the root codes (config/param/enums.yaml). */
const DOMAIN_ENUM = 'category_domain'

type LoadState = 'applied' | 'stale' | 'aborted' | 'failed'

interface RemoveState {
  node: CategoryNode
  submitting: 'delete' | 'deactivate' | null
  notice: string | null
}

function readCategoryParam(): number | null {
  try {
    const raw = new URLSearchParams(window.location.search).get('category')?.trim() ?? ''
    return /^[1-9]\d*$/.test(raw) ? Number(raw) : null
  } catch {
    return null
  }
}

/** The node's ancestors' ids, from the flat list (the tree state may be one render behind). */
function ancestorIds(nodes: readonly CategoryNode[], id: number): number[] {
  const parents = new Map(nodes.map(node => [node.id, node.parentId]))
  const ids: number[] = []
  let parentId = parents.get(id) ?? null
  while (parentId !== null && !ids.includes(parentId) && parentId !== id) {
    ids.push(parentId)
    parentId = parents.get(parentId) ?? null
  }
  return ids
}

/**
 * 食材管理: the category tree (categories + category_names) as one table, the way the other
 * management pages list their data. Every food item is a node of this tree, under one of the
 * four domains; uploads, the gallery and scene rules pick from it.
 */
export function CategoryConfigPanel({ apiBase = '' }: { apiBase?: string }) {
  const { catalog } = useEnumCatalog()
  const [nodes, setNodes] = useState<CategoryNode[]>([])
  const [heatingGroups, setHeatingGroups] = useState<HeatingGroupRef[]>([])
  const [rootCodes, setRootCodes] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [listFailure, setListFailure] = useState<CategoryFailureView | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  /** The row last opened, saved or linked to (?category=): highlighted, and kept in the URL. */
  const [selectedId, setSelectedId] = useState<number | null>(readCategoryParam)
  /** A row to scroll into view once it is listed. */
  const [reveal, setReveal] = useState<number | null>(readCategoryParam)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [filter, setFilter] = useState<CategoryTableFilter>(NO_CATEGORY_FILTER)
  const [editor, setEditor] = useState<CategoryEditorTarget | null>(null)
  const [removing, setRemoving] = useState<RemoveState | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [copiedTrace, setCopiedTrace] = useState<string | null>(null)

  const mounted = useRef(true)
  const lifetime = useRef(new AbortController())
  const listGen = useRef(0)
  const openedRoots = useRef(false)
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes

  const tree = useMemo(() => buildCategoryTree(nodes), [nodes])
  const view = useMemo(() => categoryTableView(tree, filter, expanded), [tree, filter, expanded])
  const domainName = useCallback((code: string) => enumValueLabel(catalog, DOMAIN_ENUM, code), [catalog])
  const inactiveCount = useMemo(() => nodes.filter(node => !node.isActive).length, [nodes])
  const busy = Boolean(removing?.submitting)
  const failedEmpty = Boolean(listFailure) && nodes.length === 0

  const loadTree = useCallback(async (signal: AbortSignal): Promise<LoadState> => {
    const gen = ++listGen.current
    setLoading(true)
    try {
      const list = await listCategories({ baseUrl: apiBase, signal })
      if (!mounted.current) return 'aborted'
      if (gen !== listGen.current) return 'stale'
      setNodes(list.categories)
      setHeatingGroups(list.heatingGroups)
      setRootCodes(list.rootCodes)
      setListFailure(null)
      setLoadedOnce(true)
      return 'applied'
    } catch (error) {
      if (!mounted.current) return 'aborted'
      if (error instanceof DOMException && error.name === 'AbortError') return 'aborted'
      if (gen !== listGen.current) return 'stale'
      setListFailure(toCategoryFailure(error))
      setLoadedOnce(true)
      return 'failed'
    } finally {
      if (mounted.current && gen === listGen.current) setLoading(false)
    }
  }, [apiBase])

  // The controller is made inside the effect: StrictMode runs the cleanup once, and a
  // controller created at render time would stay aborted and silently cancel every write.
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    lifetime.current = controller
    return () => {
      mounted.current = false
      controller.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void loadTree(requestSignal(controller.signal, REQUEST_TIMEOUT_MS))
    return () => controller.abort()
  }, [loadTree, reloadKey])

  // First load: open the roots, and the path to a linked node.
  useEffect(() => {
    if (openedRoots.current || nodes.length === 0) return
    openedRoots.current = true
    setExpanded(current => {
      const next = new Set(current)
      for (const node of nodes) if (node.parentId === null) next.add(node.id)
      if (selectedId !== null) for (const id of ancestorIds(nodes, selectedId)) next.add(id)
      return next
    })
  }, [nodes, selectedId])

  // Scroll a saved or linked row into view: clear a filter that hides it, open the path down to it.
  useEffect(() => {
    if (reveal === null || !loadedOnce) return
    if (view.rows.some(row => row.entry.node.id === reveal)) {
      document.getElementById(rowElementId(reveal))?.scrollIntoView({ block: 'nearest' })
      setReveal(null)
      return
    }
    if (!tree.byId.has(reveal)) {
      if (loading || listFailure) return // wait for a tree that loaded
      if (reveal === selectedId) {
        setSelectedId(null)
        setStatus(`没有找到编号为 ${reveal} 的类别，它可能已被删除。`)
      }
      setReveal(null)
      return
    }
    const path = ancestorIds(nodes, reveal)
    const root = path.length > 0 ? path[path.length - 1] : reveal
    if (view.filtering || (filter.domainId !== null && filter.domainId !== root)) setFilter(NO_CATEGORY_FILTER)
    else if (path.some(id => !expanded.has(id))) setExpanded(current => new Set([...current, ...path]))
    else setReveal(null)
  }, [reveal, view, filter.domainId, tree, nodes, expanded, loadedOnce, loading, listFailure, selectedId])

  useEffect(() => {
    try {
      const url = new URL(window.location.href)
      if (url.searchParams.get('view') !== 'categories') return
      if (selectedId !== null) url.searchParams.set('category', String(selectedId))
      else url.searchParams.delete('category')
      const next = `${url.pathname}${url.search}${url.hash}`
      const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
      if (next !== current) window.history.replaceState(null, '', next)
    } catch {
      /* Deep link updates are optional; the page works without them. */
    }
  }, [selectedId])

  function reload(): Promise<LoadState> {
    return loadTree(requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS))
  }

  function toggle(id: number) {
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function openEditor(target: CategoryEditorTarget) {
    if (busy) return
    setStatus(null)
    if (target.mode === 'edit') setSelectedId(target.node.id)
    else if (typeof target.parentId === 'number') setSelectedId(target.parentId)
    setEditor(target)
  }

  /** 新建类别 from the toolbar: a domain when the tree is empty, else the parent is chosen in the form. */
  function openCreate() {
    openEditor({ mode: 'create', parentId: nodes.length === 0 ? null : undefined })
  }

  /** Show a node the server just returned before the reload confirms it. */
  function showSaved(node: CategoryNode) {
    const previous = nodesRef.current
    const next = previous.some(entry => entry.id === node.id)
      ? previous.map(entry => (entry.id === node.id ? node : entry))
      : [...previous, node]
    nodesRef.current = next
    setNodes(next)
    setExpanded(current => new Set([...current, ...ancestorIds(next, node.id)]))
    setSelectedId(node.id)
    setReveal(node.id)
  }

  /** Create or patch; null once the server accepted it, else the failure for the caller to show. */
  async function write(request: CategorySaveRequest, done: (name: string) => string): Promise<CategoryFailureView | null> {
    const options = { baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) }
    try {
      const result = request.mode === 'create'
        ? await createCategory(request.body, options)
        : await updateCategory(request.id, request.body, options)
      if (!mounted.current) return null
      if (result.node) showSaved(result.node)
      setStatus(done(result.node ? `「${categoryLabel(result.node)}」` : '类别'))
      void reload() // a move changes the paths below it; counts and order come from the server
      return null
    } catch (error) {
      if (!mounted.current) return null
      const failure = toCategoryFailure(error)
      // The outcome is unknown, or the server refused because the tree is no longer what this
      // page loaded (a code taken meanwhile, a node gone): show what the server has now.
      if (failure.kind !== 'unknown') void reload()
      return failure
    }
  }

  async function save(request: CategorySaveRequest): Promise<CategoryFailureView | null> {
    const failure = await write(request, name => (request.mode === 'create' ? `已创建${name}。` : `已保存${name}。`))
    if (!failure && mounted.current) setEditor(null)
    return failure
  }

  function openRemove(entry: CategoryTreeNode) {
    if (busy) return
    setStatus(null)
    setSelectedId(entry.node.id)
    setRemoving({ node: entry.node, submitting: null, notice: null })
  }

  async function confirmRemove() {
    if (!removing || removing.submitting) return
    const target = removing.node
    setRemoving({ node: target, submitting: 'delete', notice: null })
    try {
      await deleteCategory(target.id, {
        baseUrl: apiBase,
        signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS),
      })
      if (!mounted.current) return
      const remaining = nodesRef.current.filter(node => node.id !== target.id)
      nodesRef.current = remaining
      setNodes(remaining)
      setSelectedId(target.parentId)
      setRemoving(null)
      setStatus(`已删除「${categoryLabel(target)}」。`)
      void reload()
    } catch (error) {
      if (!mounted.current) return
      const failure = toCategoryFailure(error)
      if (failure.kind === 'not_found') {
        setRemoving(null)
        setStatus(`「${categoryLabel(target)}」已经不存在，类别树已刷新。`)
        void reload()
        return
      }
      if (failure.kind === 'uncertain' || failure.kind === 'in_use') void reload()
      setRemoving({ node: target, submitting: null, notice: deleteFailureText(failure) })
    }
  }

  /** The way out when a category is still used: stop offering it for labelling. */
  async function deactivate() {
    if (!removing || removing.submitting) return
    const target = removing.node
    setRemoving({ node: target, submitting: 'deactivate', notice: null })
    const failure = await write({ mode: 'edit', id: target.id, body: { is_active: false } }, name => `已停用${name}。`)
    if (!mounted.current) return
    if (failure) setRemoving({ node: target, submitting: null, notice: saveFailureText(failure) })
    else setRemoving(null)
  }

  async function copyTrace(traceId: string) {
    try {
      await navigator.clipboard.writeText(traceId)
      setCopiedTrace(traceId)
    } catch {
      setCopiedTrace(null)
    }
  }

  const removingEntry = removing ? tree.byId.get(removing.node.id) ?? { node: removing.node, depth: 0, children: [] } : null
  const connection = loading && !loadedOnce ? '类别树加载中…' : failedEmpty ? '类别树加载失败' : listFailure ? '刷新失败' : '类别树已接入'

  return <div className="mg-page ct-page">
    <div className="mg-page-toolbar">
      <label className="mg-search"><Search size={17} />
        <input
          aria-label="搜索类别"
          placeholder="搜索名称、别名或编码…"
          value={filter.query}
          onChange={event => setFilter(current => ({ ...current, query: event.target.value }))}
        />
        {filter.query && <button type="button" aria-label="清空搜索" onClick={() => setFilter(current => ({ ...current, query: '' }))}><X size={15} /></button>}
      </label>
      <select
        className="mg-select"
        aria-label="类别域筛选"
        value={filter.domainId ?? ''}
        disabled={tree.roots.length === 0}
        onChange={event => setFilter(current => ({ ...current, domainId: event.target.value === '' ? null : Number(event.target.value) }))}
      >
        <option value="">全部类别域</option>
        {tree.roots.map(root => <option key={root.node.id} value={root.node.id}>{categoryLabel(root.node)}</option>)}
      </select>
      <select
        className="mg-select"
        aria-label="状态筛选"
        value={filter.status}
        onChange={event => setFilter(current => ({ ...current, status: event.target.value as CategoryStatusFilter }))}
      >
        <option value="all">全部状态</option>
        <option value="active">仅启用</option>
        <option value="inactive">仅停用</option>
      </select>
      <button type="button" className="mg-btn mg-primary" disabled={busy || failedEmpty || !loadedOnce} onClick={openCreate}><Plus size={17} />新建类别</button>
      <span className={`mg-status ct-connection${failedEmpty ? ' is-error' : loadedOnce && !listFailure ? ' is-online' : ''}`}><span />{connection}</span>
    </div>

    <p className="ct-status" role="status" aria-live="polite">{status}</p>

    <section className="mg-panel ct-panel" aria-label="类别列表">
      {listFailure && <div className="mg-errors ct-alert" role="alert">
        <div><strong>{nodes.length > 0 ? '类别树刷新失败，仍显示已有内容' : '类别树加载失败'}</strong></div>
        <p>{loadFailureText(listFailure)}</p>
        <p className="ct-trace">错误码：{listFailure.code ?? '未知'} · 追踪号：{listFailure.traceId ?? '无'}</p>
        <div className="ct-alert-actions">
          {listFailure.traceId && <button type="button" className="mg-text-button" onClick={() => void copyTrace(listFailure.traceId!)}>{copiedTrace === listFailure.traceId ? '已复制追踪号' : '复制追踪号'}</button>}
          <button type="button" className="mg-btn mg-secondary" onClick={() => setReloadKey(key => key + 1)}><RotateCcw size={15} />重试</button>
        </div>
      </div>}
      {loading && !loadedOnce && <div className="loading-state" role="status" aria-busy="true">
        <LoaderCircle size={22} className="spin" />
        <h3>正在加载类别树…</h3>
        <p>请求 GET /api/categories</p>
      </div>}
      {loadedOnce && nodes.length === 0 && !listFailure && <EmptyState
        icon={FolderTree}
        title="还没有类别"
        description="先建立类别域（食材、菜品、甜点烘焙、饮品），再在它们下面逐级添加食材类别。"
      >
        <button type="button" className="mg-btn mg-secondary" onClick={openCreate}><Plus size={16} />新建类别域</button>
      </EmptyState>}
      {nodes.length > 0 && (view.rows.length > 0
        ? <CategoryTable
          tree={tree}
          view={view}
          selectedId={selectedId}
          busy={busy}
          onToggle={toggle}
          onEdit={node => openEditor({ mode: 'edit', node })}
          onAddChild={node => openEditor({ mode: 'create', parentId: node.id })}
          onDelete={openRemove}
        />
        : <EmptyState icon={Search} title="没有匹配的类别" description="试试其他名称、别名或编码，或者换一个类别域、状态。">
          <button type="button" className="mg-btn mg-secondary" onClick={() => setFilter(NO_CATEGORY_FILTER)}>清空筛选</button>
        </EmptyState>)}
      {nodes.length > 0 && <div className="mg-pagination ct-footer">
        <span>{view.filtering
          ? `匹配 ${view.matched} 个 · 共 ${view.total} 个类别`
          : `共 ${view.total} 个类别${filter.domainId === null && inactiveCount > 0 ? ` · 停用 ${inactiveCount} 个` : ''}`}</span>
        <div>
          <button type="button" className="mg-text-button" disabled={view.filtering} onClick={() => setExpanded(new Set(nodes.map(node => node.id)))}>全部展开</button>
          <button type="button" className="mg-text-button" disabled={view.filtering} onClick={() => setExpanded(new Set())}>全部收起</button>
          <button type="button" className="mg-text-button" disabled={loading} onClick={() => { setStatus(null); setReloadKey(key => key + 1) }}>{loading && loadedOnce ? '正在刷新…' : '刷新'}</button>
        </div>
      </div>}
    </section>

    <div className="mg-info"><AlertTriangle size={17} /><p>这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，已入库图片的场景归属不会自动重算，需要到「场景管理」执行同步。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。</p></div>

    {editor && <CategoryEditorDialog
      target={editor}
      tree={tree}
      heatingGroups={heatingGroups}
      rootCodes={rootCodes}
      domainName={domainName}
      save={save}
      onClose={() => setEditor(null)}
    />}

    {removing && removingEntry && <CategoryDeleteDialog
      entry={removingEntry}
      submitting={removing.submitting}
      notice={removing.notice}
      onClose={() => setRemoving(null)}
      onDelete={() => void confirmRemove()}
      onDeactivate={() => void deactivate()}
    />}
  </div>
}
