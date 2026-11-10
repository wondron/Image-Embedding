import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, FolderTree, LoaderCircle, Pencil, Plus, Trash2 } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import {
  buildCategoryTree,
  categoryAncestors,
  categoryLabel,
  createCategory,
  deleteCategory,
  effectiveCuisines,
  effectiveHeatingGroup,
  listCategories,
  toCategoryFailure,
  updateCategory,
  type CategoryFailureView,
  type CategoryNode,
  type CategoryTreeNode,
  type HeatingGroupRef,
} from '../../entities/category'
import { enumValueLabel, useEnumCatalog } from '../../entities/enum'
import { formatLibraryTime, requestSignal } from '../../shared/lib'
import { EmptyState } from '../../shared/ui'
import { CategoryEditorDialog, type CategoryEditorTarget, type CategorySaveRequest } from './CategoryEditorDialog'
import { deleteBlockReason, deleteFailureText, loadFailureText } from './messages'
import '../../components/ManagementViews.css'
import './category-config.css'

const REQUEST_TIMEOUT_MS = 20_000
/** The system enum whose labels name the root codes (config/param/enums.yaml). */
const DOMAIN_ENUM = 'category_domain'

type LoadState = 'applied' | 'stale' | 'aborted' | 'failed'

interface RemoveState {
  node: CategoryNode
  submitting: boolean
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

function TreeRows({ entries, expanded, selectedId, onToggle, onSelect }: {
  entries: readonly CategoryTreeNode[]
  expanded: ReadonlySet<number>
  selectedId: number | null
  onToggle: (id: number) => void
  onSelect: (id: number) => void
}) {
  return <ul className="cat-tree-list">
    {entries.map(entry => {
      const { node, children } = entry
      const label = categoryLabel(node)
      const open = expanded.has(node.id)
      return <li key={node.id}>
        <div className="cat-row">
          {children.length > 0
            ? <button
              type="button"
              className="cat-toggle"
              aria-expanded={open}
              aria-label={`${open ? '收起' : '展开'}「${label}」`}
              onClick={() => onToggle(node.id)}
            >{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
            : <span className="cat-toggle cat-toggle-leaf" aria-hidden="true" />}
          <button
            type="button"
            className="cat-node"
            aria-current={node.id === selectedId ? 'true' : undefined}
            onClick={() => onSelect(node.id)}
          >
            <strong>{label}</strong>
            <code>{node.code}</code>
            {!node.isActive && <span className="cat-badge">停用</span>}
            {children.length > 0 && <span className="cat-count" aria-label={`${children.length} 个子类别`}>{children.length}</span>}
          </button>
        </div>
        {open && children.length > 0 && <TreeRows entries={children} expanded={expanded} selectedId={selectedId} onToggle={onToggle} onSelect={onSelect} />}
      </li>
    })}
  </ul>
}

export function CategoryConfigPanel({ apiBase = '' }: { apiBase?: string }) {
  const { catalog } = useEnumCatalog()
  const [nodes, setNodes] = useState<CategoryNode[]>([])
  const [heatingGroups, setHeatingGroups] = useState<HeatingGroupRef[]>([])
  const [rootCodes, setRootCodes] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [listFailure, setListFailure] = useState<CategoryFailureView | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [selectedId, setSelectedId] = useState<number | null>(readCategoryParam)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [mobilePane, setMobilePane] = useState<'tree' | 'detail'>(readCategoryParam() === null ? 'tree' : 'detail')
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
  const selected = selectedId === null ? undefined : tree.byId.get(selectedId)
  const domainName = useCallback((code: string) => enumValueLabel(catalog, DOMAIN_ENUM, code), [catalog])
  const usedCodes = useMemo(() => new Set(nodes.map(node => node.code)), [nodes])
  const freeRootCodes = rootCodes.filter(code => !usedCodes.has(code))
  const busy = Boolean(removing?.submitting)

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

  // First load: open the roots, and the path to a deep-linked node.
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
      /* Deep link updates are optional; selection still works in the page. */
    }
  }, [selectedId])

  function reload(): Promise<LoadState> {
    return loadTree(requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS))
  }

  function select(id: number) {
    setSelectedId(id)
    setMobilePane('detail')
    setStatus(null) // the last result belongs to the previous step
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
    setEditor(target)
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
    select(node.id)
  }

  async function save(request: CategorySaveRequest): Promise<CategoryFailureView | null> {
    const options = { baseUrl: apiBase, signal: requestSignal(lifetime.current.signal, REQUEST_TIMEOUT_MS) }
    try {
      const result = request.mode === 'create'
        ? await createCategory(request.body, options)
        : await updateCategory(request.id, request.body, options)
      if (!mounted.current) return null
      setEditor(null)
      if (result.node) showSaved(result.node)
      const name = result.node ? `「${categoryLabel(result.node)}」` : '类别'
      setStatus(request.mode === 'create' ? `已创建${name}。` : `已保存${name}。`)
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

  async function confirmRemove() {
    if (!removing || removing.submitting) return
    const target = removing.node
    setRemoving({ node: target, submitting: true, notice: null })
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
      setRemoving({ node: target, submitting: false, notice: deleteFailureText(failure) })
    }
  }

  async function copyTrace(traceId: string) {
    try {
      await navigator.clipboard.writeText(traceId)
      setCopiedTrace(traceId)
    } catch {
      setCopiedTrace(null)
    }
  }

  const blockReason = selected ? deleteBlockReason(selected) : null
  const cuisines = selected ? effectiveCuisines(tree, selected.node.id) : null
  const heatingGroup = selected ? effectiveHeatingGroup(tree, selected.node.id) : null
  const ancestors = selected ? categoryAncestors(tree, selected.node.id) : []
  const newRootButton = <button
    type="button"
    className="mg-btn mg-primary"
    disabled={busy || freeRootCodes.length === 0}
    title={loadedOnce && !listFailure && freeRootCodes.length === 0 ? '可用的根类别都已建立' : undefined}
    onClick={() => openEditor({ mode: 'create', parent: null })}
  ><Plus size={14} />新建根类别</button>

  return <div className="cat-page">
    {listFailure && <div className="mg-errors cat-alert" role="alert">
      <strong>{nodes.length > 0 ? '类别树刷新失败，仍显示已有内容' : '类别树加载失败'}</strong>
      <p>错误码：{listFailure.code ?? '未知'}</p>
      <p>{loadFailureText(listFailure)}</p>
      <p className="cat-trace">追踪号：{listFailure.traceId ?? '无'}</p>
      <div className="cat-alert-actions">
        {listFailure.traceId && <button type="button" className="mg-text-button" onClick={() => void copyTrace(listFailure.traceId!)}>{copiedTrace === listFailure.traceId ? '已复制追踪号' : '复制追踪号'}</button>}
        <button type="button" className="mg-btn mg-secondary" onClick={() => setReloadKey(key => key + 1)}>重试</button>
      </div>
    </div>}
    <p className="cat-status" role="status" aria-live="polite">{status}</p>

    <div className={`cat-split ${mobilePane === 'detail' ? 'is-detail' : 'is-tree'}`}>
      <section className="mg-panel cat-pane cat-tree" aria-label="类别树">
        <div className="cat-pane-heading">
          <h2>类别树{nodes.length > 0 && <span className="cat-total">{nodes.length}</span>}</h2>
          <div className="cat-pane-tools">
            <button type="button" className="mg-text-button" disabled={nodes.length === 0} onClick={() => setExpanded(new Set(nodes.map(node => node.id)))}>全部展开</button>
            <button type="button" className="mg-text-button" disabled={nodes.length === 0} onClick={() => setExpanded(new Set())}>全部收起</button>
            <button type="button" className="mg-text-button" onClick={() => { setStatus(null); setReloadKey(key => key + 1) }}>{loading && loadedOnce ? '正在刷新…' : '刷新'}</button>
          </div>
        </div>
        <div className="cat-tree-actions">{newRootButton}</div>
        {loading && !loadedOnce && <div className="loading-state" role="status" aria-busy="true">
          <LoaderCircle size={22} className="spin" />
          <h3>正在加载类别树…</h3>
          <p>请求 GET /api/categories</p>
        </div>}
        {loadedOnce && nodes.length === 0 && !listFailure && <EmptyState
          icon={FolderTree}
          title="还没有类别"
          description="先建立根类别（食材、菜品、甜点烘焙、饮品这几个域），再在它们下面逐级添加子类别。"
        />}
        {nodes.length > 0 && <div className="cat-tree-scroll">
          <TreeRows entries={tree.roots} expanded={expanded} selectedId={selectedId} onToggle={toggle} onSelect={select} />
        </div>}
      </section>

      <section className="mg-panel cat-pane cat-detail" aria-label="类别详情">
        <button type="button" className="mg-btn mg-secondary cat-back" onClick={() => setMobilePane('tree')}>返回类别树</button>
        {selectedId === null && <EmptyState icon={FolderTree} title="请选择左侧类别" description="选中后可以查看它的名称和属性，新建子类别、编辑、移动或删除。" />}
        {selectedId !== null && !selected && <div className="cat-detail-body">
          <p className="cat-note" role="status">{!loadedOnce
            ? '正在读取类别…'
            : listFailure && nodes.length === 0
              ? '类别树还没有加载出来。'
              : `类别树里没有编号为 ${selectedId} 的类别，可能已被删除。`}</p>
        </div>}
        {selected && <div className="cat-detail-body">
          <div className="cat-detail-heading">
            <div className="cat-detail-title">
              <h2>{categoryLabel(selected.node)}</h2>
              <code>{selected.node.code}</code>
              {!selected.node.isActive && <span className="cat-badge">停用</span>}
            </div>
            <div className="cat-detail-actions">
              <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={() => openEditor({ mode: 'create', parent: selected.node })}><Plus size={14} />新建子类别</button>
              <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={() => openEditor({ mode: 'edit', node: selected.node })}><Pencil size={14} />编辑</button>
              <button
                type="button"
                className="mg-btn mg-secondary cat-danger"
                disabled={busy || blockReason !== null}
                aria-describedby={blockReason ? 'category-delete-block' : undefined}
                onClick={() => { setStatus(null); setRemoving({ node: selected.node, submitting: false, notice: null }) }}
              ><Trash2 size={14} />删除</button>
            </div>
          </div>
          {blockReason && <p className="cat-note" id="category-delete-block">{blockReason}</p>}
          <dl className="cat-meta">
            <dt>上级</dt>
            <dd>{ancestors.length > 0
              ? ancestors.map((ancestor, index) => <span key={ancestor.id}>
                {index > 0 && ' / '}
                <button type="button" className="mg-text-button" onClick={() => select(ancestor.id)}>{categoryLabel(ancestor)}</button>
              </span>)
              : '根节点'}</dd>
            <dt>名称</dt>
            <dd>{selected.node.names.length > 0
              ? <ul className="cat-names-list">{selected.node.names.map(name => <li key={`${name.lang}\n${name.name}`}>
                <span className="cat-lang">{name.lang}</span>{name.name}
                <span className="cat-kind">{name.kind === 'primary' ? '主名' : '别名'}</span>
              </li>)}</ul>
              : '还没有名称'}</dd>
            <dt>菜系</dt>
            <dd>{cuisines && cuisines.value.length > 0
              ? <>{cuisines.value.join('、')}{cuisines.from && <span className="cat-inherited">沿用「{categoryLabel(cuisines.from)}」</span>}</>
              : '未设置'}</dd>
            <dt>加热组</dt>
            <dd>{heatingGroup?.value
              ? <>{heatingGroup.value.name}（<code>{heatingGroup.value.code}</code>）{heatingGroup.from && <span className="cat-inherited">沿用「{categoryLabel(heatingGroup.from)}」</span>}</>
              : '未设置'}</dd>
            <dt>同级排序</dt><dd>{selected.node.sortOrder}</dd>
            <dt>状态</dt><dd>{selected.node.isActive ? '启用' : '停用'}</dd>
            <dt>子类别</dt><dd>{selected.children.length} 个</dd>
            <dt>图片</dt><dd>{selected.node.imageCount} 张（直接标在这个类别上的）</dd>
            <dt>场景规则</dt><dd>{selected.node.sceneRuleCount} 条</dd>
            <dt>更新时间</dt><dd>{formatLibraryTime(selected.node.updatedAt)}</dd>
          </dl>
        </div>}
      </section>
    </div>

    <div className="mg-info"><AlertTriangle size={17} /><p>移动类别或修改菜系后，已入库图片的场景归属不会自动重算。只有没有子类别、没有图片、也没有场景规则引用的类别才能删除。加热组和场景规则不在本页管理。</p></div>

    {editor && <CategoryEditorDialog
      target={editor}
      tree={tree}
      heatingGroups={heatingGroups}
      rootCodes={rootCodes}
      domainName={domainName}
      save={save}
      onClose={() => setEditor(null)}
    />}

    {removing && <Dialog title="删除类别" size="sm" dismissDisabled={removing.submitting} onClose={() => setRemoving(null)}>
      <div className="cat-confirm">
        <p>确定删除「{categoryLabel(removing.node)}」（<code>{removing.node.code}</code>）吗？它的全部名称会一起删除，删除后不能恢复。</p>
        {removing.notice && <p className="mg-form-error" role="alert">{removing.notice}</p>}
        <div className="dialog-actions">
          <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={removing.submitting} onClick={() => setRemoving(null)}>取消</button>
          <button type="button" className="mg-btn cat-danger-solid" disabled={removing.submitting} onClick={() => void confirmRemove()}>
            {removing.submitting && <LoaderCircle size={14} className="spin" />}
            {removing.submitting ? '删除中…' : '删除'}
          </button>
        </div>
      </div>
    </Dialog>}
  </div>
}
