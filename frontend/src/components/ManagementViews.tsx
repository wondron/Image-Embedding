import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowRight, Check, CheckCircle2, ChevronLeft, ChevronRight, Circle, Clock3, CloudUpload, FolderOpen, ImagePlus, Images, Info, Layers3, Leaf, ListChecks, LoaderCircle, Pencil, Plus, RotateCcw, Search, Square, Tags, Trash2, X, XCircle } from 'lucide-react'
import { Dialog } from './Dialog'
import { indexImage } from '../lib/index-api'
import { listImages, type LibraryImageItem } from '../lib/images-api'
import './ManagementViews.css'

export type ManagementView = 'library' | 'import' | 'tasks' | 'ingredients'
type Props = { view: ManagementView; onNavigate: (view: ManagementView) => void; apiBase?: string }
type IndexJobStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled'
type IndexJob = {
  id: string
  file: File
  preview: string
  status: IndexJobStatus
  elapsed?: number
  error?: string
  remoteId?: string | null
}
type Ingredient = { id: string; name: string; category: string; attributes: string }
const categories = ['蔬菜', '水果', '肉禽', '水产', '蛋奶', '粮油', '调味品', '其他']
const MAX_IMAGE_SIZE = 15 * 1024 * 1024
const MAX_QUEUE = 100
const INDEX_TIMEOUT_MS = 120_000
const taskStatuses = ['全部任务', '处理中', '已完成', '失败记录']
const jobStatusText: Record<IndexJobStatus, string> = {
  pending: '待入库', running: '正在入库', success: '已入库', error: '入库失败', cancelled: '已取消，可继续',
}
let nextDraftId = 0
function draftId() { return `draft-${++nextDraftId}` }

function supportsDirectoryPicker(): boolean {
  try {
    return typeof HTMLInputElement !== 'undefined' && 'webkitdirectory' in HTMLInputElement.prototype
  } catch {
    return false
  }
}

function EmptyState({ icon: Icon, title, description, children }: {
  icon: typeof Images; title: string; description: string; children?: ReactNode
}) {
  return <div className="mg-empty"><span className="mg-empty-icon"><Icon size={29} strokeWidth={1.4} /></span>
    <h3>{title}</h3><p>{description}</p>{children}</div>
}

export function ManagementViews({ view, onNavigate, apiBase = '' }: Props) {
  const [libraryQuery, setLibraryQuery] = useState('')
  const [libraryCategory, setLibraryCategory] = useState('全部类别')
  const [libraryLayout, setLibraryLayout] = useState<'grid' | 'list'>('grid')
  const [libraryPage, setLibraryPage] = useState(1)
  const [debouncedLibraryQuery, setDebouncedLibraryQuery] = useState('')
  const [libraryItems, setLibraryItems] = useState<LibraryImageItem[]>([])
  const [libraryTotal, setLibraryTotal] = useState(0)
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [libraryConnected, setLibraryConnected] = useState(false)
  const [libraryPreview, setLibraryPreview] = useState<LibraryImageItem | null>(null)
  const [libraryReloadKey, setLibraryReloadKey] = useState(0)
  const libraryAbort = useRef<AbortController | null>(null)
  const [taskStatus, setTaskStatus] = useState('全部任务')
  const [jobs, setJobs] = useState<IndexJob[]>([])
  const jobsRef = useRef(jobs)
  jobsRef.current = jobs
  const [fileIssues, setFileIssues] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [importCategory, setImportCategory] = useState('未分类')
  const [preview, setPreview] = useState<IndexJob | null>(null)
  const [indexing, setIndexing] = useState(false)
  const indexingRef = useRef(false)
  const runToken = useRef(0)
  const activeIndex = useRef<{ controller: AbortController; jobId: string; timer: number } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const dirInput = useRef<HTMLInputElement>(null)
  const [dirSupported] = useState(supportsDirectoryPicker)
  const [ingredients, setIngredients] = useState<Ingredient[]>([])
  const [ingredientQuery, setIngredientQuery] = useState('')
  const [ingredientCategory, setIngredientCategory] = useState('全部类别')
  const [editor, setEditor] = useState<Ingredient | null>(null)
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')
  const apiBaseRef = useRef(apiBase)
  apiBaseRef.current = apiBase
  const prevApiBase = useRef(apiBase)

  useEffect(() => () => {
    runToken.current++
    if (activeIndex.current) {
      window.clearTimeout(activeIndex.current.timer)
      activeIndex.current.controller.abort('unmounted')
      activeIndex.current = null
    }
    jobsRef.current.forEach(item => URL.revokeObjectURL(item.preview))
  }, [])

  useEffect(() => {
    if (prevApiBase.current !== apiBase) {
      prevApiBase.current = apiBase
      if (indexingRef.current) {
        stopIndexing()
        setNotice('服务地址已改变，当前入库已停止。可继续未完成的图片。')
      }
    }
  }, [apiBase])

  // Debounce search box → q (~300ms); empty trim omits q on request.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedLibraryQuery(libraryQuery.trim())
      setLibraryPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [libraryQuery])

  // Fetch GET /api/images when library view is active (depends on backend §3.5).
  useEffect(() => {
    if (view !== 'library') return
    libraryAbort.current?.abort()
    const controller = new AbortController()
    libraryAbort.current = controller
    const requestBase = apiBaseRef.current
    setLibraryLoading(true)
    setLibraryError(null)
    void listImages({
      page: libraryPage,
      pageSize: 24,
      q: debouncedLibraryQuery || undefined,
      category: libraryCategory === '全部类别' ? undefined : libraryCategory,
      scene: 'normal',
      baseUrl: requestBase,
      signal: controller.signal,
    }).then(result => {
      if (controller.signal.aborted) return
      const maxPage = Math.max(1, Math.ceil(result.total / 24) || 1)
      if (result.total > 0 && result.items.length === 0 && libraryPage > maxPage) {
        setLibraryPage(maxPage)
        return
      }
      setLibraryItems(result.items)
      setLibraryTotal(result.total)
      setLibraryConnected(true)
      setLibraryLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted || (cause instanceof Error && cause.name === 'AbortError')) return
      setLibraryItems([])
      setLibraryTotal(0)
      setLibraryError(cause instanceof Error ? cause.message : '图库列表加载失败，请重试。')
      setLibraryLoading(false)
    })
    return () => controller.abort()
  }, [view, libraryPage, debouncedLibraryQuery, libraryCategory, apiBase, libraryReloadKey])

    const commitJobs = useCallback((next: IndexJob[]) => {
    jobsRef.current = next
    setJobs(next)
  }, [])

  function addFiles(files: FileList | File[]) {
    if (indexingRef.current) return
    const issues: string[] = []
    const additions: IndexJob[] = []
    const existing = new Set(jobsRef.current.map(item => `${item.file.name}:${item.file.size}:${item.file.lastModified}`))
    for (const file of Array.from(files)) {
      const key = `${file.name}:${file.size}:${file.lastModified}`
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        issues.push(`${file.name}：仅支持 JPG、PNG、WebP 图片`)
      } else if (file.size === 0 || file.size > MAX_IMAGE_SIZE) {
        issues.push(`${file.name}：图片不能为空，且不能超过 15 MB`)
      } else if (existing.has(key)) {
        issues.push(`${file.name}：已在待入库列表中`)
      } else if (jobsRef.current.length + additions.length >= MAX_QUEUE) {
        issues.push(`${file.name}：单次最多准备 100 张图片`)
      } else {
        existing.add(key)
        additions.push({ id: draftId(), file, preview: URL.createObjectURL(file), status: 'pending' })
      }
    }
    if (additions.length) commitJobs([...jobsRef.current, ...additions])
    setFileIssues(issues)
  }

  function removeJob(id: string) {
    if (indexingRef.current) return
    const item = jobsRef.current.find(job => job.id === id)
    if (item) URL.revokeObjectURL(item.preview)
    commitJobs(jobsRef.current.filter(job => job.id !== id))
    if (preview?.id === id) setPreview(null)
  }

  function clearQueue() {
    if (indexingRef.current) return
    jobsRef.current.forEach(item => URL.revokeObjectURL(item.preview))
    commitJobs([])
    setPreview(null)
    setFileIssues([])
  }

  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
  }

  function stopIndexing() {
    runToken.current++
    if (activeIndex.current) {
      window.clearTimeout(activeIndex.current.timer)
      activeIndex.current.controller.abort('cancelled')
      activeIndex.current = null
    }
    commitJobs(jobsRef.current.map(job => job.status === 'running' ? { ...job, status: 'cancelled', error: undefined } : job))
    indexingRef.current = false
    setIndexing(false)
  }

  async function startIndexing(retryFailed = false) {
    if (indexingRef.current) return
    const targets = jobsRef.current.filter(job => retryFailed
      ? job.status === 'error' || job.status === 'cancelled'
      : job.status === 'pending' || job.status === 'cancelled')
    if (!targets.length) {
      setNotice(retryFailed ? '没有可重试的失败或已取消项。' : '请先添加待入库图片。')
      return
    }
    const token = ++runToken.current
    const requestBase = apiBaseRef.current
    const category = importCategory
    indexingRef.current = true
    setIndexing(true)
    setNotice('')
    setFileIssues([])
    const currentRun = () => runToken.current === token && apiBaseRef.current === requestBase
    try {
      for (const target of targets) {
        if (!currentRun()) break
        const controller = new AbortController()
        const timer = window.setTimeout(() => controller.abort('timeout'), INDEX_TIMEOUT_MS)
        activeIndex.current = { controller, jobId: target.id, timer }
        commitJobs(jobsRef.current.map(job => job.id === target.id
          ? { ...job, status: 'running', error: undefined }
          : job))
        const started = performance.now()
        try {
          const result = await indexImage({
            file: target.file,
            category,
            baseUrl: requestBase,
            signal: controller.signal,
          })
          if (!currentRun()) break
          if (controller.signal.aborted) throw new Error('入库超时')
          commitJobs(jobsRef.current.map(job => job.id === target.id
            ? {
              ...job,
              status: 'success',
              remoteId: result.indexed[0]?.id ?? null,
              elapsed: (performance.now() - started) / 1000,
              error: undefined,
            }
            : job))
        } catch (cause) {
          if (!currentRun()) break
          const error = controller.signal.aborted && controller.signal.reason === 'timeout'
            ? '入库超过 120 秒，请稍后重试。'
            : cause instanceof Error ? cause.message : '入库失败，请重试。'
          commitJobs(jobsRef.current.map(job => job.id === target.id
            ? { ...job, status: 'error', error, elapsed: (performance.now() - started) / 1000 }
            : job))
        } finally {
          window.clearTimeout(timer)
          if (activeIndex.current?.controller === controller) activeIndex.current = null
        }
      }
    } finally {
      if (runToken.current === token) {
        indexingRef.current = false
        setIndexing(false)
      }
    }
  }

  function saveIngredient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editor) return
    const name = editor.name.trim()
    if (!name) { setFormError('请输入食材名称'); return }
    if (ingredients.some(item => item.id !== editor.id && item.name === name)) {
      setFormError('此食材名称已存在，请使用其他名称'); return
    }
    const updated = { ...editor, name, attributes: editor.attributes.trim() }
    setIngredients(current => current.some(item => item.id === updated.id)
      ? current.map(item => item.id === updated.id ? updated : item)
      : [...current, updated])
    setEditor(null)
    setNotice(`已保存「${name}」的本地草稿`)
  }

  function openEditor(item?: Ingredient) {
    setFormError('')
    setEditor(item ?? { id: draftId(), name: '', category: '蔬菜', attributes: '' })
  }

  function navigateTaskTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const targetIndex = event.key === 'ArrowRight' ? (index + 1) % taskStatuses.length
      : event.key === 'ArrowLeft' ? (index + taskStatuses.length - 1) % taskStatuses.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? taskStatuses.length - 1 : null
    if (targetIndex === null) return
    event.preventDefault()
    setTaskStatus(taskStatuses[targetIndex])
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#task-tab-${targetIndex}`)?.focus()
  }

  const filteredIngredients = ingredients.filter(item =>
    (ingredientCategory === '全部类别' || item.category === ingredientCategory) &&
    `${item.name} ${item.attributes}`.toLocaleLowerCase().includes(ingredientQuery.trim().toLocaleLowerCase()))

  const doneCount = jobs.filter(job => job.status === 'success' || job.status === 'error').length
  const successCount = jobs.filter(job => job.status === 'success').length
  const failureCount = jobs.filter(job => job.status === 'error').length
  const runningCount = jobs.filter(job => job.status === 'running').length
  const resumable = jobs.some(job => job.status === 'pending' || job.status === 'cancelled')
  const retryable = jobs.some(job => job.status === 'error' || job.status === 'cancelled')
  const hasStarted = jobs.some(job => job.status !== 'pending')
  const totalBytes = jobs.reduce((total, item) => total + item.file.size, 0)

  const taskRows = jobs.filter(job => {
    if (taskStatus === '处理中') return job.status === 'running' || job.status === 'pending' || job.status === 'cancelled'
    if (taskStatus === '已完成') return job.status === 'success'
    if (taskStatus === '失败记录') return job.status === 'error'
    return true
  })

  return <div className="mg-page">
    <div className="mg-page-toolbar">
      {view === 'library' && <>
        <label className="mg-search"><Search size={17} /><input aria-label="搜索图库" placeholder="搜索图片名称或食材…" value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} />{libraryQuery && <button aria-label="清空搜索" onClick={() => setLibraryQuery('')}><X size={15} /></button>}</label>
        <select className="mg-select" aria-label="图库类别筛选" value={libraryCategory} onChange={event => { setLibraryCategory(event.target.value); setLibraryPage(1) }}><option>全部类别</option>{categories.map(category => <option key={category}>{category}</option>)}</select>
        <div className="mg-view-toggle" aria-label="图片展示方式"><button className={libraryLayout === 'grid' ? 'active' : ''} aria-label="网格展示" aria-pressed={libraryLayout === 'grid'} onClick={() => setLibraryLayout('grid')}><Images size={17} /></button><button className={libraryLayout === 'list' ? 'active' : ''} aria-label="列表展示" aria-pressed={libraryLayout === 'list'} onClick={() => setLibraryLayout('list')}><ListChecks size={17} /></button></div>
        <button className="mg-btn mg-primary" onClick={() => onNavigate('import')}><Plus size={17} />导入图片</button>
      </>}
      {view === 'import' && <>
        <button className="mg-btn mg-primary" disabled={indexing} onClick={() => fileInput.current?.click()}><FolderOpen size={17} />选择图片</button>
        {dirSupported
          ? <button className="mg-btn mg-secondary" disabled={indexing} onClick={() => dirInput.current?.click()}><FolderOpen size={17} />选择目录</button>
          : <span className="mg-toolbar-summary" title="当前浏览器不支持目录选择">目录选择不可用</span>}
        <span className="mg-toolbar-summary">JPG、PNG、WebP · 每张最大 15 MB · 最多 100 张</span>
        <input ref={fileInput} type="file" className="mg-hidden" aria-label="选择待入库图片" accept="image/jpeg,image/png,image/webp" multiple onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />
        {dirSupported && <input ref={dirInput} type="file" className="mg-hidden" aria-label="选择待入库目录" accept="image/jpeg,image/png,image/webp" multiple {...({ webkitdirectory: '', directory: '' } as object)} onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />}
      </>}
      {view === 'tasks' && <>
        <div className="mg-tabs" role="tablist" aria-label="任务状态">{taskStatuses.map((status, index) => <button key={status} id={`task-tab-${index}`} role="tab" aria-selected={taskStatus === status} aria-controls="task-panel" tabIndex={taskStatus === status ? 0 : -1} className={taskStatus === status ? 'active' : ''} onClick={() => setTaskStatus(status)} onKeyDown={event => navigateTaskTabs(event, index)}>{status}</button>)}</div>
        <button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}><Plus size={16} />准备图片</button>
      </>}
      {view === 'ingredients' && <>
        <label className="mg-search"><Search size={17} /><input aria-label="搜索食材草稿" placeholder="搜索食材名称或属性…" value={ingredientQuery} onChange={event => setIngredientQuery(event.target.value)} />{ingredientQuery && <button aria-label="清空食材搜索" onClick={() => setIngredientQuery('')}><X size={15} /></button>}</label>
        <select className="mg-select" aria-label="食材类别筛选" value={ingredientCategory} onChange={event => setIngredientCategory(event.target.value)}><option>全部类别</option>{categories.map(category => <option key={category}>{category}</option>)}</select>
        <span className="mg-toolbar-summary">{ingredients.length} 个食材草稿</span>
        <button className="mg-btn mg-primary" onClick={() => openEditor()}><Plus size={17} />新建食材</button>
      </>}
      <span className="mg-status"><span />{view === 'ingredients' ? '本地草稿模式' : view === 'import' || view === 'tasks' ? (indexing ? '入库进行中' : '自建入库已接入') : libraryLoading ? '图库加载中…' : libraryError ? '图库加载失败' : libraryConnected ? '图库已接入' : '图库未连接'}</span>
    </div>

    {view === 'library' && (() => {
      const hasFilter = Boolean(debouncedLibraryQuery) || libraryCategory !== '全部类别'
      const totalPages = Math.max(1, Math.ceil(libraryTotal / 24) || 1)
      const pageLabel = libraryTotal === 0 ? '—' : `${libraryPage} / ${totalPages}`
      return <>
      <div className="mg-metrics">
        <Metric label="图片总量" value={libraryError ? '—' : String(libraryTotal)} detail={libraryConnected ? '当前筛选合计' : '接入图库后显示'} icon={Images} />
        <Metric label="食材类别" value="—" detail="后续接入" icon={Layers3} />
        <Metric label="待入库任务" value={jobs.length ? String(jobs.filter(j => j.status !== 'success').length) : '—'} detail={jobs.length ? '本会话未完成项' : '接入任务后显示'} icon={ListChecks} />
      </div>
      <section className="mg-panel">
        {libraryLayout === 'list' && <div className="mg-table-head mg-library-head"><span>图片 / 名称</span><span>类别</span><span>属性</span><span>入库时间</span></div>}
        {libraryError && <div className="mg-errors" role="alert"><div><strong>图库加载失败</strong><button type="button" className="mg-text-button" onClick={() => setLibraryReloadKey(key => key + 1)}>重试</button></div><p>{libraryError}</p></div>}
        {!libraryError && libraryLoading && libraryItems.length === 0 && (
          <div className="loading-state" aria-busy="true" aria-live="polite">
            <LoaderCircle size={22} className="spin" />
            <h3>正在加载图库…</h3>
            <p>请求 GET /api/images（每页 24 张）</p>
            <div className="skeleton-row" aria-hidden="true"><div className="skeleton-card" /><div className="skeleton-card" /><div className="skeleton-card" /></div>
          </div>
        )}
        {!libraryError && !libraryLoading && libraryTotal === 0 && !hasFilter && (
          <EmptyState icon={Images} title="暂无已入库图片" description="可先整理本地图片，再提交到自建向量库。">
            <button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}><ImagePlus size={17} />准备第一批图片<ArrowRight size={16} /></button>
          </EmptyState>
        )}
        {!libraryError && !libraryLoading && libraryTotal === 0 && hasFilter && (
          <EmptyState icon={Images} title="没有匹配的图片" description="当前搜索或类别下没有结果，试试清空筛选。">
            <button className="mg-btn mg-secondary" onClick={() => { setLibraryQuery(''); setLibraryCategory('全部类别'); setLibraryPage(1) }}>清空筛选</button>
          </EmptyState>
        )}
        {!libraryError && libraryItems.length > 0 && libraryLayout === 'grid' && (
          <div className={`mg-upload-grid mg-library-grid${libraryLoading ? ' is-loading' : ''}`} aria-busy={libraryLoading}>
            {libraryItems.map(item => (
              <article className="mg-upload-card mg-library-card" key={item.id}>
                <button
                  type="button"
                  className="mg-upload-preview"
                  disabled={!item.imageUrl}
                  onClick={() => item.imageUrl && setLibraryPreview(item)}
                  aria-label={item.imageUrl ? `预览 ${item.title ?? item.id}` : '暂无预览'}
                >
                  {item.imageUrl
                    ? <img src={item.imageUrl} alt={item.title ?? ''} loading="lazy" />
                    : <span className="mg-library-placeholder" aria-hidden="true"><Images size={28} strokeWidth={1.4} /></span>}
                  <span>{item.imageUrl ? '查看图片' : '暂无预览'}</span>
                </button>
                <div>
                  <strong title={item.title ?? item.id}>{item.title ?? '未命名图片'}</strong>
                  <small>
                    {item.category ? <span className="mg-category-chip">{item.category}</span> : <span>—</span>}
                    {item.scenes.length > 0 && <span className="mg-library-scene">{item.scenes[0]}</span>}
                  </small>
                </div>
              </article>
            ))}
          </div>
        )}
        {!libraryError && libraryItems.length > 0 && libraryLayout === 'list' && (
          <div className={`mg-library-list${libraryLoading ? ' is-loading' : ''}`} role="list" aria-busy={libraryLoading}>
            {libraryItems.map(item => (
              <div className="mg-library-row mg-table-row" role="listitem" key={item.id}>
                <button
                  type="button"
                  className="mg-library-name"
                  disabled={!item.imageUrl}
                  onClick={() => item.imageUrl && setLibraryPreview(item)}
                >
                  {item.imageUrl
                    ? <img src={item.imageUrl} alt="" className="mg-library-thumb" loading="lazy" />
                    : <span className="mg-library-thumb mg-library-placeholder" aria-hidden="true"><Images size={16} /></span>}
                  <strong title={item.title ?? item.id}>{item.title ?? '未命名图片'}</strong>
                </button>
                <span>{item.category ? <span className="mg-category-chip">{item.category}</span> : '—'}</span>
                <span className="mg-attributes">{item.tags.length ? item.tags.join('、') : '—'}</span>
                <span>{formatLibraryTime(item.createdAt)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="mg-pagination">
          <span>{libraryError ? '加载失败' : libraryLoading && libraryItems.length === 0 ? '加载中…' : libraryTotal === 0 ? '暂无已入库图片' : `共 ${libraryTotal} 张`}</span>
          <div>
            <span>每页 24 张</span>
            <button aria-label="上一页" disabled={libraryLoading || libraryPage <= 1 || libraryTotal === 0} onClick={() => setLibraryPage(page => Math.max(1, page - 1))}><ChevronLeft size={16} /></button>
            <span>{pageLabel}</span>
            <button aria-label="下一页" disabled={libraryLoading || libraryPage >= totalPages || libraryTotal === 0} onClick={() => setLibraryPage(page => page + 1)}><ChevronRight size={16} /></button>
          </div>
        </div>
      </section>
      <div className="mg-info"><Info size={17} /><p>图库列表来自 <code>GET /api/images</code>（scene=normal，每页 24）。缩略图只用 <code>image_url</code>；本刀不做软删。本会话入库进度见「入库任务」。</p></div>
    </>
    })()}

    {view === 'import' && <div className="mg-import-layout"><section className="mg-panel mg-import-main">
      <div className={`mg-dropzone ${dragging ? 'is-dragging' : ''} ${indexing ? 'is-disabled' : ''}`} onDragOver={event => { event.preventDefault(); if (!indexing) setDragging(true) }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}>
        <span className="mg-drop-icon"><CloudUpload size={28} strokeWidth={1.5} /></span><div><h3>把图片拖到这里</h3><p>或使用顶部的「选择图片」{dirSupported ? ' / 「选择目录」' : ''}添加文件</p></div>
      </div>
      {fileIssues.length > 0 && <div className="mg-errors" role="alert"><div><strong>部分图片未添加</strong><button onClick={() => setFileIssues([])} aria-label="关闭文件提示"><X size={16} /></button></div><ul>{fileIssues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div>}
      <div className="mg-queue-heading"><h3>待入库队列 <span>{jobs.length}</span></h3>{jobs.length > 0 && <button className="mg-text-button" disabled={indexing} onClick={clearQueue}>清空列表</button>}</div>
      {jobs.length === 0 ? <div className="mg-queue-empty"><Images size={22} /><p>选好的图片会在这里显示</p></div> : <div className="mg-upload-grid">{jobs.map(item => <article className={`mg-upload-card mg-job-${item.status}`} key={item.id}>
        <button className="mg-upload-preview" onClick={() => setPreview(item)} aria-label={`预览 ${item.file.name}`}><img src={item.preview} alt={item.file.name} /><span>查看图片</span></button>
        {!indexing && <button className="mg-remove-image" aria-label={`移除 ${item.file.name}`} onClick={() => removeJob(item.id)}><X size={14} /></button>}
        <div><strong title={item.file.name}>{item.file.name}</strong>
          <small>{(item.file.size / 1024 / 1024).toFixed(2)} MB <span className={`mg-job-status mg-job-status-${item.status}`}>{item.status === 'running' ? <LoaderCircle size={12} className="spin" /> : item.status === 'success' ? <CheckCircle2 size={12} /> : item.status === 'error' ? <XCircle size={12} /> : item.status === 'cancelled' ? <Clock3 size={12} /> : <Circle size={11} />}{jobStatusText[item.status]}</span></small>
          {item.remoteId && <small className="mg-job-id" title={item.remoteId}>id: {item.remoteId}</small>}
          {item.error && <small className="mg-job-error">{item.error}</small>}
          {item.elapsed !== undefined && (item.status === 'success' || item.status === 'error') && <small className="mg-job-elapsed">{item.elapsed.toFixed(1)} 秒</small>}
        </div></article>)}</div>}
    </section><aside className="mg-import-aside"><section className="mg-panel mg-import-settings"><h2>整理入库信息</h2><p>为本次图片选择统一类别，然后提交到自建向量库。</p><label className="mg-field">食材类别<select className="mg-select" disabled={indexing} value={importCategory} onChange={event => setImportCategory(event.target.value)}><option>未分类</option>{categories.map(category => <option key={category}>{category}</option>)}</select></label><div className="mg-import-summary"><span>已选择图片<strong>{jobs.length} 张</strong></span><span>文件总大小<strong>{(totalBytes / 1024 / 1024).toFixed(2)} MB</strong></span><span>默认类别<strong>{importCategory}</strong></span></div>
      {jobs.length > 0 && <div className="mg-index-progress" aria-live="polite">
        <div><b>{indexing ? '正在入库' : doneCount === jobs.length && hasStarted ? '本批入库完成' : '入库进度'}</b><span>{doneCount} / {jobs.length}</span></div>
        <progress aria-label="入库进度" value={doneCount} max={Math.max(jobs.length, 1)} />
        <p>成功 {successCount} 张{failureCount > 0 ? ` · 失败 ${failureCount} 张` : ''}{jobs.some(job => job.status === 'cancelled') ? ' · 可继续已取消项' : ''}</p>
      </div>}
      <div className="mg-index-actions">
        {indexing
          ? <button type="button" className="mg-btn mg-secondary mg-full" onClick={stopIndexing}><Square size={15} />取消入库</button>
          : <button type="button" className="mg-btn mg-primary mg-full" disabled={!resumable} onClick={() => void startIndexing(false)}><CloudUpload size={17} />{hasStarted && resumable ? '继续入库' : '开始入库'}</button>}
        {!indexing && retryable && <button type="button" className="mg-btn mg-secondary mg-full" onClick={() => void startIndexing(true)}><RotateCcw size={15} />仅重试失败</button>}
      </div>
      <p className="mg-help">逐张调用 <code>POST /api/index</code>。进行中不可清空队列；离开页面后本会话队列不保留。批量端点形态待契约冻结后可改。</p></section>
      <div className="mg-workflow-note"><ListChecks size={18} /><h3>入库任务</h3><p>本批进度也会汇总到任务中心；无本会话任务时保持空态。</p><button className="mg-text-button" onClick={() => onNavigate('tasks')}>查看任务中心<ArrowRight size={15} /></button></div></aside></div>}

    {view === 'tasks' && <>
      <div className="mg-metrics">
        <Metric label="正在处理" value={jobs.length ? String(runningCount + jobs.filter(j => j.status === 'pending' || j.status === 'cancelled').length) : '—'} detail={jobs.length ? '本会话队列' : '暂无本会话任务'} icon={CloudUpload} />
        <Metric label="成功入库" value={jobs.length ? String(successCount) : '—'} detail={jobs.length ? '本会话成功' : '暂无本会话任务'} icon={Check} />
        <Metric label="失败记录" value={jobs.length ? String(failureCount) : '—'} detail={jobs.length ? '本会话失败' : '暂无本会话任务'} icon={ListChecks} />
      </div>
      <section className="mg-panel"><div id="task-panel" role="tabpanel" aria-labelledby={`task-tab-${taskStatuses.indexOf(taskStatus)}`}>
        <div className="mg-table-head mg-task-head"><span>任务名称</span><span>处理进度</span><span>状态</span><span>标识 / 原因</span></div>
        {taskRows.length === 0
          ? <EmptyState icon={ListChecks} title={taskStatus === '失败记录' ? '暂无可查看的失败记录' : taskStatus === '处理中' ? '暂无正在处理的任务' : taskStatus === '已完成' ? '暂无已完成的任务' : '暂无入库任务'} description={jobs.length ? '当前筛选下没有匹配的本会话任务。' : '在「上传与导入」开始入库后，这里会显示本会话进度与失败原因。'}><button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}>先准备一批图片<ArrowRight size={16} /></button></EmptyState>
          : <div className="mg-task-list" role="list">{taskRows.map(job => <div className={`mg-task-row mg-job-${job.status}`} role="listitem" key={job.id}>
            <div className="mg-task-name"><img src={job.preview} alt="" className="mg-task-thumb" /><strong title={job.file.name}>{job.file.name}</strong></div>
            <div className="mg-task-progress">{job.elapsed !== undefined ? `${job.elapsed.toFixed(1)} 秒` : indexing && job.status === 'running' ? '进行中…' : '—'}</div>
            <div className={`mg-job-status mg-job-status-${job.status}`}>{job.status === 'running' ? <LoaderCircle size={13} className="spin" /> : job.status === 'success' ? <CheckCircle2 size={13} /> : job.status === 'error' ? <XCircle size={13} /> : job.status === 'cancelled' ? <Clock3 size={13} /> : <Circle size={12} />}{jobStatusText[job.status]}</div>
            <div className="mg-task-detail" title={job.remoteId ?? job.error ?? ''}>{job.remoteId ? `id: ${job.remoteId}` : job.error ? job.error : '—'}</div>
          </div>)}</div>}
      </div></section>
      <div className="mg-info"><Info size={17} /><p>任务列表仅反映本浏览器会话的入库队列，刷新页面后清空；不使用假进度或假成功数据。</p></div>
    </>}

    {view === 'ingredients' && <>
      <section className="mg-panel">
        <div className="mg-ingredient-table"><div className="mg-table-head mg-ingredient-row"><span>食材名称</span><span>类别</span><span>食材属性</span><span>操作</span></div>{filteredIngredients.length ? filteredIngredients.map(item => <div className="mg-ingredient-row mg-table-row" key={item.id}><strong><span className="mg-leaf-icon"><Leaf size={17} /></span>{item.name}</strong><span><span className="mg-category-chip">{item.category}</span></span><span className="mg-attributes">{item.attributes || '未填写'}</span><div className="mg-row-actions"><button aria-label={`编辑 ${item.name}`} onClick={() => openEditor(item)}><Pencil size={16} /></button><button aria-label={`删除 ${item.name}`} onClick={() => { setIngredients(current => current.filter(ingredient => ingredient.id !== item.id)); setNotice(`已删除「${item.name}」的本地草稿`) }}><Trash2 size={16} /></button></div></div>) : <EmptyState icon={Tags} title={ingredients.length ? '没有匹配的食材草稿' : '暂无食材草稿'} description={ingredients.length ? '试试其他食材名称、属性或类别。' : '从名称、类别和属性开始，先在本地整理你的食材数据。'}>{ingredients.length ? <button className="mg-btn mg-secondary" onClick={() => { setIngredientQuery(''); setIngredientCategory('全部类别') }}>重置筛选</button> : <button className="mg-btn mg-secondary" onClick={() => openEditor()}><Plus size={16} />新建食材草稿</button>}</EmptyState>}</div>
      </section><div className="mg-info"><Info size={17} /><p>食材数据接口尚未接入。本地草稿不会写入数据库，刷新页面或离开后将清空。</p></div>
    </>}

    <div className="mg-live-status" role="status" aria-live="polite">{notice}</div>
    {preview && <Dialog title="图片预览" onClose={() => setPreview(null)} wide><img className="mg-dialog-image" src={preview.preview} alt={preview.file.name} /><p className="mg-preview-name">{preview.file.name}</p>{preview.remoteId && <p className="mg-preview-name monospace">入库 id：{preview.remoteId}</p>}{preview.error && <p className="mg-form-error" role="alert">{preview.error}</p>}</Dialog>}
    {libraryPreview?.imageUrl && <Dialog title="图片预览" onClose={() => setLibraryPreview(null)} wide><img className="mg-dialog-image" src={libraryPreview.imageUrl} alt={libraryPreview.title ?? ''} /><p className="mg-preview-name">{libraryPreview.title ?? libraryPreview.id}</p>{libraryPreview.category && <p className="mg-preview-name">{libraryPreview.category}</p>}</Dialog>}
    {editor && <Dialog title={ingredients.some(item => item.id === editor.id) ? '编辑食材草稿' : '新建食材草稿'} onClose={() => setEditor(null)}>
      <form className="mg-editor" onSubmit={saveIngredient}>
        <div className="mg-editor-body">
          <p className="mg-editor-note"><Info size={14} />本地草稿，尚未连接数据库</p>
          <section className="mg-editor-section" aria-labelledby="ingredient-basic-title">
            <h3 id="ingredient-basic-title">基本信息</h3>
            <label className="mg-field"><span className="mg-field-label">食材名称<span aria-hidden="true"> *</span></span>
              <input autoFocus required maxLength={60} placeholder="例如：西红柿" value={editor.name} onChange={event => setEditor({ ...editor, name: event.target.value })} />
            </label>
            <label className="mg-field"><span className="mg-field-label">食材类别</span>
              <select value={editor.category} onChange={event => setEditor({ ...editor, category: event.target.value })}>{categories.map(category => <option key={category}>{category}</option>)}</select>
            </label>
          </section>
          <section className="mg-editor-section" aria-labelledby="ingredient-attributes-title">
            <h3 id="ingredient-attributes-title">属性信息</h3>
            <label className="mg-field"><span className="mg-field-label">食材属性</span>
              <textarea rows={3} maxLength={300} placeholder="例如：红色、圆形、可生食（多个属性以顿号分隔）" value={editor.attributes} onChange={event => setEditor({ ...editor, attributes: event.target.value })} />
              <small>可补充颜色、形态、产地等信息，最多 300 字</small>
            </label>
          </section>
          {formError && <p className="mg-form-error" role="alert">{formError}</p>}
        </div>
        <div className="mg-editor-actions"><button type="button" className="mg-btn mg-secondary" onClick={() => setEditor(null)}>取消</button><button type="submit" className="mg-btn mg-primary"><Check size={15} />保存本地草稿</button></div>
      </form>
    </Dialog>}
  </div>
}

function formatLibraryTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function Metric({ label, value, detail, icon: Icon }: { label: string; value: string; detail: string; icon: typeof Images }) {
  return <div className="mg-metric"><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div><span className="mg-metric-icon"><Icon size={22} strokeWidth={1.5} /></span></div>
}
