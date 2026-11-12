import { useCallback, useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { ArrowRight, Check, CheckCircle2, Circle, Clock3, CloudUpload, FolderOpen, Images, Info, ListChecks, LoaderCircle, Pencil, Plus, RotateCcw, Square, X, XCircle } from 'lucide-react'
import { Dialog } from './Dialog'
import { TasksPage } from '../pages/tasks'
import { LibraryPage } from '../pages/library'
import { categoryLabel, type CategoryTree } from '../entities/category'
import { indexImage } from '../entities/image'
import { sceneDisplayLabel, sceneError, writeMembership, type VectorSync } from '../entities/scene'
import { EmptyState, Metric, notify } from '../shared/ui'
import { useCategoryTree } from '../features/category-picker'
import { INITIAL_LIBRARY_FILTERS, type LibraryFilters } from '../features/library-filters'
import {
  AnnotationForm,
  emptyAnnotation,
  filledExtras,
  hasErrors,
  membershipSteps,
  toIndexFields,
  validateAnnotation,
  type ImportAnnotation,
  type MembershipStep,
  type SceneCatalogView,
} from '../features/index-upload'
import { markScenePending, useSceneCatalog } from '../features/scene-catalog'
import { beginSceneWrite, endSceneWrite } from '../features/scene-sync'
import './ManagementViews.css'

export type ManagementView = 'library' | 'import' | 'tasks' | 'categories' | 'enums' | 'scenes'
type Props = { view: ManagementView; onNavigate: (view: ManagementView) => void; apiBase?: string }
type IndexJobStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled'
/** What the server stored for a job, and what is worth telling about it. */
type JobOutcome = { categoryId: number | null; scenes: string[]; created: boolean | null; notes: string[] }
type IndexJob = {
  id: string
  file: File
  preview: string
  status: IndexJobStatus
  elapsed?: number
  error?: string
  remoteId?: string | null
  /** The image's own annotation (单独标注); undefined: the batch annotation applies. */
  annotation?: ImportAnnotation
  outcome?: JobOutcome
}
const MAX_IMAGE_SIZE = 15 * 1024 * 1024
const MAX_QUEUE = 100
const INDEX_TIMEOUT_MS = 120_000
const taskStatuses = ['全部任务', '处理中', '已完成', '失败记录']
const jobStatusText: Record<IndexJobStatus, string> = {
  pending: '待入库', running: '正在入库', success: '已入库', error: '入库失败', cancelled: '已取消，可继续',
}
let nextDraftId = 0
function draftId() { return `draft-${++nextDraftId}` }

/** After a manual membership write: what its vector_sync means for this upload (pushed: nothing to say). */
const VECTOR_SYNC_NOTE: Record<Exclude<VectorSync, 'pushed'>, string> = {
  missing: '场景已保存，但这张图在当前向量库里没有向量。',
  not_ready: '场景已保存；检索服务未就绪，检索要等同步后才能看到。',
  failed: '场景已保存；更新检索索引失败，可到场景管理执行同步。',
}

/**
 * Write the scene choices of one stored image (manual include / exclude), one at a time.
 * Returns the image's scenes after the last successful write (null: none succeeded) and notes.
 */
async function applySceneSteps(base: string, imageId: string, steps: readonly MembershipStep[], stillRunning: () => boolean) {
  let scenes: string[] | null = null
  const notes: string[] = []
  for (const [index, step] of steps.entries()) {
    if (!stillRunning()) {
      notes.push(`入库已停止，以下场景调整没有写入：${steps.slice(index).map(item => item.name).join('、')}。`)
      break
    }
    if (!beginSceneWrite()) {
      notes.push('全量场景同步进行中，场景调整没有写入，可稍后在图库里调整。')
      break
    }
    try {
      const written = await writeMembership(base, imageId, step.sceneId, step.isMember)
      scenes = written.scenes
      if (written.syncRequired) markScenePending(base)
      if (written.vectorSync && written.vectorSync !== 'pushed') notes.push(VECTOR_SYNC_NOTE[written.vectorSync])
    } catch (cause) {
      notes.push(`${step.isMember ? '纳入' : '排除'}「${step.name}」没有写入：${sceneError(cause)}`)
    } finally {
      endSceneWrite()
    }
  }
  return { scenes, notes: [...new Set(notes)] }
}

/** 「牛排」 / 「未设类别」 for a card; the id when the tree does not have the node. */
function categoryText(tree: CategoryTree | null, categoryId: number | null): string {
  if (categoryId === null) return '未设类别'
  const node = tree?.byId.get(categoryId)?.node
  return node ? categoryLabel(node) : `类别 #${categoryId}`
}

function supportsDirectoryPicker(): boolean {
  try {
    return typeof HTMLInputElement !== 'undefined' && 'webkitdirectory' in HTMLInputElement.prototype
  } catch {
    return false
  }
}

export function ManagementViews({ view, onNavigate, apiBase = '' }: Props) {
  // The library's filters and page live here, so they survive a visit to 上传与导入 or 入库任务
  const [libraryFilters, setLibraryFilters] = useState<LibraryFilters>(INITIAL_LIBRARY_FILTERS)
  const [libraryPage, setLibraryPage] = useState(1)
  const [taskStatus, setTaskStatus] = useState('全部任务')
  const [jobs, setJobs] = useState<IndexJob[]>([])
  const jobsRef = useRef(jobs)
  jobsRef.current = jobs
  const [dragging, setDragging] = useState(false)
  const [batchAnnotation, setBatchAnnotation] = useState<ImportAnnotation>(emptyAnnotation)
  /** The image being annotated on its own, with the dialog's unsaved draft. */
  const [editing, setEditing] = useState<{ jobId: string; draft: ImportAnnotation } | null>(null)
  const [preview, setPreview] = useState<IndexJob | null>(null)
  const [indexing, setIndexing] = useState(false)
  const indexingRef = useRef(false)
  const runToken = useRef(0)
  const activeIndex = useRef<{ controller: AbortController; jobId: string; timer: number } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const dirInput = useRef<HTMLInputElement>(null)
  const [dirSupported] = useState(supportsDirectoryPicker)
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
        notify.warn('服务地址已改变，当前入库已停止。可继续未完成的图片。', { title: '入库已停止' })
      }
    }
  }, [apiBase])

  const sceneCatalog = useSceneCatalog(apiBase)
  // Read again each time the import or library page opens: categories may have been edited meanwhile
  const categoryTree = useCategoryTree(apiBase, view === 'import' || view === 'library')
  const sceneView: SceneCatalogView = { active: sceneCatalog.active, loading: sceneCatalog.loading, error: sceneCatalog.error, refresh: sceneCatalog.refresh }

  // What keeps 「开始入库」 disabled, worded for the person
  const runnable = jobs.filter(job => job.status !== 'success' && job.status !== 'running')
  const batchErrors = validateAnnotation(batchAnnotation, categoryTree.tree)
  const usesBatch = runnable.some(job => !job.annotation)
  const invalidSingles = runnable.filter(job => job.annotation && hasErrors(validateAnnotation(job.annotation, categoryTree.tree))).length
  const wantsSceneWrites = (usesBatch && Object.keys(batchAnnotation.sceneOverrides).length > 0)
    || runnable.some(job => job.annotation && Object.keys(job.annotation.sceneOverrides).length > 0)
  const blockers = [
    usesBatch && hasErrors(batchErrors) ? '批量标注里有需要修改的内容，见上方标红的字段。' : null,
    invalidSingles ? `${invalidSingles} 张单独标注的图片有需要修改的内容，请在卡片上打开「编辑单独标注」。` : null,
    wantsSceneWrites && !sceneCatalog.valid ? '场景目录还没读到，暂时不能写入场景调整。' : null,
  ].filter((text): text is string => text !== null)
  const importReady = blockers.length === 0

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
    if (issues.length) notify.warn(additions.length ? `已添加 ${additions.length} 张，另有 ${issues.length} 张没有添加：` : `${issues.length} 张图片没有添加：`, { title: '部分图片未添加', details: issues })
  }

  function removeJob(id: string) {
    if (indexingRef.current) return
    const item = jobsRef.current.find(job => job.id === id)
    if (item) URL.revokeObjectURL(item.preview)
    commitJobs(jobsRef.current.filter(job => job.id !== id))
    if (preview?.id === id) setPreview(null)
    if (editing?.jobId === id) setEditing(null)
  }

  function clearQueue() {
    if (indexingRef.current) return
    jobsRef.current.forEach(item => URL.revokeObjectURL(item.preview))
    commitJobs([])
    setPreview(null)
    setEditing(null)
  }

  const openSceneSync = useCallback(() => {
    onNavigate('scenes')
    const url = new URL(window.location.href)
    url.searchParams.set('sceneTab', 'sync')
    window.history.replaceState(null, '', url.toString())
  }, [onNavigate])

  /** Open the single-image dialog on the image's own annotation, else a copy of the batch's. */
  function openSingle(job: IndexJob) {
    if (indexingRef.current || job.status === 'success') return
    setEditing({ jobId: job.id, draft: job.annotation ?? batchAnnotation })
  }

  /** annotation null: back to the batch annotation. */
  function saveSingle(jobId: string, annotation: ImportAnnotation | null) {
    if (indexingRef.current) return
    commitJobs(jobsRef.current.map(job => job.id === jobId ? { ...job, annotation: annotation ?? undefined } : job))
    setEditing(null)
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
    if (!importReady) { notify.warn(blockers[0] ?? '标注里有需要修改的内容。', { title: '暂时不能入库' }); return }
    const targets = jobsRef.current.filter(job => retryFailed
      ? job.status === 'error' || job.status === 'cancelled'
      : job.status === 'pending' || job.status === 'cancelled')
    if (!targets.length) {
      notify.info(retryFailed ? '没有可重试的失败或已取消项。' : '请先添加待入库图片。')
      return
    }
    const token = ++runToken.current
    const requestBase = apiBaseRef.current
    // This run sends the annotations and catalogs as they are now
    const batch = batchAnnotation
    const tree = categoryTree.tree
    const scenes = sceneCatalog.active
    indexingRef.current = true
    setIndexing(true)
    setEditing(null)
    const currentRun = () => runToken.current === token && apiBaseRef.current === requestBase
    const updateJob = (id: string, patch: (job: IndexJob) => Partial<IndexJob>) =>
      commitJobs(jobsRef.current.map(job => job.id === id ? { ...job, ...patch(job) } : job))
    try {
      for (const target of targets) {
        if (!currentRun()) break
        const annotation = target.annotation ?? batch
        const controller = new AbortController()
        const timer = window.setTimeout(() => controller.abort('timeout'), INDEX_TIMEOUT_MS)
        activeIndex.current = { controller, jobId: target.id, timer }
        updateJob(target.id, () => ({ status: 'running', error: undefined, outcome: undefined }))
        const started = performance.now()
        try {
          const result = await indexImage({
            file: target.file,
            ...toIndexFields(annotation, tree),
            baseUrl: requestBase,
            signal: controller.signal,
          })
          if (!currentRun()) break
          if (controller.signal.aborted) throw new Error('入库超时')
          window.clearTimeout(timer)
          const image = result.indexed[0]
          const outcome: JobOutcome = {
            categoryId: image?.categoryId ?? null,
            scenes: image?.scenes ?? [],
            created: image?.created ?? null,
            notes: image?.created === false
              ? ['相同图片已在图库中，沿用原有标注，本次填写的内容没有写入；需要修改请到图库编辑。']
              : [],
          }
          updateJob(target.id, () => ({
            status: 'success',
            remoteId: image?.id ?? null,
            elapsed: (performance.now() - started) / 1000,
            error: undefined,
            outcome,
          }))
          // Scene choices against the rules: manual include / exclude on the new image
          const steps = image && image.created !== false ? membershipSteps(annotation.sceneOverrides, image.scenes, scenes) : []
          if (image && steps.length) {
            const written = await applySceneSteps(requestBase, image.id, steps, currentRun)
            updateJob(target.id, job => ({
              outcome: { ...outcome, scenes: written.scenes ?? outcome.scenes, notes: [...(job.outcome?.notes ?? []), ...written.notes] },
            }))
          }
        } catch (cause) {
          if (!currentRun()) break
          const error = controller.signal.aborted && controller.signal.reason === 'timeout'
            ? '入库超过 120 秒，请稍后重试。'
            : sceneError(cause)
          updateJob(target.id, () => ({ status: 'error', error, elapsed: (performance.now() - started) / 1000 }))
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

  function navigateTaskTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const targetIndex = event.key === 'ArrowRight' ? (index + 1) % taskStatuses.length
      : event.key === 'ArrowLeft' ? (index + taskStatuses.length - 1) % taskStatuses.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? taskStatuses.length - 1 : null
    if (targetIndex === null) return
    event.preventDefault()
    setTaskStatus(taskStatuses[targetIndex])
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#task-tab-${targetIndex}`)?.focus()
  }

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

  return <div className={`mg-page${view === 'library' ? ' mg-page-library' : view === 'import' ? ' mg-page-import' : ''}`}>
    {view === 'import' && <div className="mg-import-meta">
      <span className="mg-toolbar-summary">JPG、PNG、WebP · 每张最大 15 MB · 最多 100 张</span>
      <span className="mg-status"><span />{indexing ? '入库进行中' : '自建入库已接入'}</span>
    </div>}

    {view === 'library' && <LibraryPage
      apiBase={apiBase}
      filters={libraryFilters}
      setFilters={setLibraryFilters}
      page={libraryPage}
      setPage={setLibraryPage}
      categories={categoryTree}
      pendingImports={jobs.filter(job => job.status !== 'success').length}
      onImport={() => onNavigate('import')}
      onOpenSceneSync={openSceneSync}
    />}

    {view === 'import' && <div className="mg-import-layout"><section className="mg-panel mg-import-main">
      <div className="mg-pickup-row">
        <button className="mg-btn mg-primary" disabled={indexing} onClick={() => fileInput.current?.click()}><FolderOpen size={17} />选择图片</button>
        {dirSupported
          ? <button className="mg-btn mg-secondary" disabled={indexing} onClick={() => dirInput.current?.click()}><FolderOpen size={17} />选择目录</button>
          : <span className="mg-toolbar-summary" title="当前浏览器不支持目录选择">目录选择不可用</span>}
        <div className={`mg-dropzone ${dragging ? 'is-dragging' : ''} ${indexing ? 'is-disabled' : ''}`} onDragOver={event => { event.preventDefault(); if (!indexing) setDragging(true) }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}>
          <span className="mg-drop-icon"><CloudUpload size={28} strokeWidth={1.5} /></span><div><h3>把图片拖到这里</h3><p>或使用左侧{dirSupported ? '「选择图片」/「选择目录」' : '按钮'}添加文件</p></div>
        </div>
        <input ref={fileInput} type="file" className="mg-hidden" aria-label="选择待入库图片" accept="image/jpeg,image/png,image/webp" multiple onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />
        {dirSupported && <input ref={dirInput} type="file" className="mg-hidden" aria-label="选择待入库目录" accept="image/jpeg,image/png,image/webp" multiple {...({ webkitdirectory: '', directory: '' } as object)} onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />}
      </div>
      <div className="mg-queue-heading"><h3>待入库队列 <span>{jobs.length}</span></h3>{jobs.length > 0 && <button className="mg-text-button" disabled={indexing} onClick={clearQueue}>清空列表</button>}</div>
      {jobs.length === 0 ? <div className="mg-queue-empty"><Images size={22} /><p>选好的图片会在这里显示</p></div> : <div className="mg-upload-grid">{jobs.map(item => {
        const annotation = item.annotation ?? batchAnnotation
        const shownCategory = item.outcome ? item.outcome.categoryId : annotation.categoryId
        const extras = item.outcome ? 0 : filledExtras(annotation)
        return <article className={`mg-upload-card mg-job-${item.status}`} key={item.id}>
          <button className="mg-upload-preview" onClick={() => setPreview(item)} aria-label={`预览 ${item.file.name}`}><img src={item.preview} alt={item.file.name} /><span>查看图片</span></button>
          {!indexing && <button className="mg-remove-image" aria-label={`移除 ${item.file.name}`} onClick={() => removeJob(item.id)}><X size={14} /></button>}
          <div><strong title={item.file.name}>{item.file.name}</strong>
            <small>{(item.file.size / 1024 / 1024).toFixed(2)} MB <span className={`mg-job-status mg-job-status-${item.status}`}>{item.status === 'running' ? <LoaderCircle size={12} className="spin" /> : item.status === 'success' ? <CheckCircle2 size={12} /> : item.status === 'error' ? <XCircle size={12} /> : item.status === 'cancelled' ? <Clock3 size={12} /> : <Circle size={11} />}{jobStatusText[item.status]}</span></small>
            <div className="mg-job-annotation">
              <span className={`mg-category-chip${shownCategory === null ? ' is-empty' : ''}`}>{categoryText(categoryTree.tree, shownCategory)}</span>
              {item.annotation && !item.outcome && <span className="mg-job-custom">单独标注</span>}
              {extras > 0 && <span className="mg-job-extras">另填 {extras} 项</span>}
            </div>
            {item.outcome && item.outcome.scenes.length > 0 && <div className="mg-job-scenes" aria-label="所属场景">{item.outcome.scenes.map(code => <span className="mg-library-scene" key={code}>{sceneCatalog.data?.scenes.find(s => s.code === code)?.name ?? sceneDisplayLabel(code)}</span>)}</div>}
            {item.status !== 'success' && <button type="button" className="mg-text-button mg-job-edit" disabled={indexing} onClick={() => openSingle(item)}><Pencil size={13} />{item.annotation ? '编辑单独标注' : '单独标注'}</button>}
            {item.remoteId && <small className="mg-job-id" title={item.remoteId}>id: {item.remoteId}</small>}
            {item.outcome?.notes.map(note => <small className="mg-job-note" key={note}>{note}</small>)}
            {item.error && <small className="mg-job-error">{item.error}</small>}
            {item.elapsed !== undefined && (item.status === 'success' || item.status === 'error') && <small className="mg-job-elapsed">{item.elapsed.toFixed(1)} 秒</small>}
          </div></article>
      })}</div>}
    </section><aside className="mg-import-aside"><section className="mg-panel mg-import-settings"><div className="mg-import-settings-head"><h2>批量标注</h2><p>对本批没有「单独标注」的图片统一生效，入库时一起写入。</p></div>
      <div className="mg-import-settings-body">
      <AnnotationForm value={batchAnnotation} onChange={setBatchAnnotation} errors={batchErrors} categories={categoryTree} scenes={sceneView} disabled={indexing} />
      <p className="mg-help">逐张调用 <code>POST /api/index</code>；有场景调整的图片入库后再写人工纳入 / 排除。字节完全相同的图片只入库一次，再次上传沿用原有标注。进行中不可清空队列；切换到入库任务仍保留本会话队列，整页刷新或离开数据管理宿主后不保留。停止等待不保证服务端未成功。</p>
      </div>
      <div className="mg-import-run">
      <p className="mg-import-tally"><span>已选 <strong>{jobs.length}</strong> 张 · {(totalBytes / 1024 / 1024).toFixed(2)} MB</span>{jobs.some(job => job.annotation) && <span>单独标注 <strong>{jobs.filter(job => job.annotation).length}</strong> 张</span>}</p>
      {jobs.length > 0 && <div className="mg-index-progress" aria-live="polite">
        <div><b>{indexing ? '正在入库' : doneCount === jobs.length && hasStarted ? '本批入库完成' : '入库进度'}</b><span>{doneCount} / {jobs.length}</span></div>
        <progress aria-label="入库进度" value={doneCount} max={Math.max(jobs.length, 1)} />
        <p>成功 {successCount} 张{failureCount > 0 ? ` · 失败 ${failureCount} 张` : ''}{jobs.some(job => job.status === 'cancelled') ? ' · 可继续已取消项' : ''}</p>
      </div>}
      {!indexing && (resumable || retryable) && blockers.length > 0 && <ul className="mg-import-blockers" role="alert">{blockers.map(text => <li key={text}>{text}</li>)}</ul>}
      <div className="mg-index-actions">
        {indexing
          ? <button type="button" className="mg-btn mg-secondary mg-full" onClick={stopIndexing}><Square size={15} />取消入库</button>
          : <button type="button" className="mg-btn mg-primary mg-full" disabled={!resumable || !importReady} onClick={() => void startIndexing(false)}><CloudUpload size={17} />{hasStarted && resumable ? '继续入库' : '开始入库'}</button>}
        {!indexing && retryable && <button type="button" className="mg-btn mg-secondary mg-full" disabled={!importReady} onClick={() => void startIndexing(true)}><RotateCcw size={15} />仅重试失败</button>}
      </div>
      </div></section>
      <div className="mg-workflow-note"><ListChecks size={18} /><h3>入库任务</h3><p>在任务中心的「本会话上传队列」查看本批进度；服务端入库记录只显示已记录的同步终态，可能缺行。</p><button className="mg-text-button" onClick={() => onNavigate('tasks')}>查看任务中心<ArrowRight size={15} /></button></div></aside></div>}

    {view === 'tasks' && <TasksPage apiBase={apiBase} queue={<>
      <div className="mg-page-toolbar">
        <div className="mg-tabs" role="tablist" aria-label="任务状态">{taskStatuses.map((status, index) => <button key={status} id={`task-tab-${index}`} role="tab" aria-selected={taskStatus === status} aria-controls="task-panel" tabIndex={taskStatus === status ? 0 : -1} className={taskStatus === status ? 'active' : ''} onClick={() => setTaskStatus(status)} onKeyDown={event => navigateTaskTabs(event, index)}>{status}</button>)}</div>
        <button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}><Plus size={16} />准备图片</button>
      </div>
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
      <div className="mg-info"><Info size={17} /><p>这里只显示本会话上传队列。刷新或离开数据管理宿主后不保留；浏览器停止等待、超时或离开不保证服务器未成功或未记记录。后续人工场景调整与 notes 只属本会话，不是历史记录的全流程结果。</p></div>
    </>} />}

    {preview && <Dialog title="图片预览" onClose={() => setPreview(null)} wide><img className="mg-dialog-image" src={preview.preview} alt={preview.file.name} /><p className="mg-preview-name">{preview.file.name}</p>{preview.remoteId && <p className="mg-preview-name monospace">入库 id：{preview.remoteId}</p>}{preview.error && <p className="mg-form-error" role="alert">{preview.error}</p>}</Dialog>}
    {editing && (() => {
      const job = jobs.find(item => item.id === editing.jobId)
      if (!job) return null
      const draftErrors = validateAnnotation(editing.draft, categoryTree.tree)
      return <Dialog title="单张标注" size="lg" onClose={() => setEditing(null)} footer={<div className="ia-dialog-actions">
        {job.annotation && <button type="button" className="mg-btn mg-secondary ia-dialog-reset" onClick={() => saveSingle(job.id, null)}>改回批量设置</button>}
        <button type="button" className="mg-btn mg-secondary" onClick={() => setEditing(null)}>取消</button>
        <button type="button" className="mg-btn mg-primary" disabled={hasErrors(draftErrors)} onClick={() => saveSingle(job.id, editing.draft)}><Check size={15} />保存单独标注</button>
      </div>}>
        <div className="ia-single">
          <div className="ia-single-preview">
            <img src={job.preview} alt={job.file.name} />
            <div><small title={job.file.name}>{job.file.name}</small>
              <small>{job.annotation ? '这张图片使用自己的标注，不跟随批量设置。' : '从批量设置复制而来；保存后这张图片不再跟随批量设置。'}</small></div>
          </div>
          <AnnotationForm value={editing.draft} onChange={draft => setEditing({ jobId: job.id, draft })} errors={draftErrors} categories={categoryTree} scenes={sceneView} />
        </div>
      </Dialog>
    })()}
  </div>
}


