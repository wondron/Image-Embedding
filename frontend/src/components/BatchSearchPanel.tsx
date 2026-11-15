import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, Circle, Clock3, Images, LoaderCircle, Play, RefreshCw, RotateCcw, Square, Trash2, UploadCloud, X, XCircle } from 'lucide-react'
import { searchAutoImages, searchGlobalImages, searchLocalBatch, type SearchResponse } from '../entities/search-result'
import { maxBatchImages, selectBatchFiles } from '../lib/batch-search'
import { ImageFilePicker } from './ImageFilePicker'
import { notify } from '../shared/ui'
import './BatchSearchPanel.css'

type JobStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled'
interface SearchJob {
  id: string
  file: File
  preview: string
  status: JobStatus
  response?: SearchResponse
  elapsed?: number
  error?: string
  /** Where the shown result (or failure) came from. */
  via?: BatchSearchRequest['target']
}

export interface BatchSearchResult {
  response: SearchResponse
  file: File
  /** Elapsed seconds for this query, excluding other queued images. */
  elapsed: number
}

/**
 * Where a run searches, and with what (read when the run starts, so every image of a run gets the same):
 * - local: 自建库 through 多图搜索 (POST /api/search/batch/files), LOCAL_BATCH_SIZE images per request;
 * - global: 现网 Global, one request per image;
 * - auto: 业务搜图 (自建库优先, Global 兜底), one request per image.
 */
export type BatchSearchRequest =
  | { target: 'local'; scene: string; /** null = the server default; 0 = no threshold. */ scoreThreshold: number | null }
  | { target: 'global'; /** omitted = the server default. */ searchNum?: number }
  | { target: 'auto'; scene: string; scoreThreshold: number | null; topK: number; globalFallback: boolean }

interface BatchSearchPanelProps {
  baseUrl: string
  request: BatchSearchRequest
  /** A reason a run cannot start now (e.g. the scene catalog is not loaded); shown when one is tried. */
  blocked?: string | null
  active?: boolean
  onResult: (payload: BatchSearchResult) => void
  onClearResult?: () => void
  onRunningChange?: (running: boolean) => void
}

/** 自建库: query images sent per request — progress and 「停止」 work between requests. */
const LOCAL_BATCH_SIZE = 5
const REQUEST_TIMEOUT_MS = 120_000

const statusText: Record<JobStatus, string> = {
  pending: '等待搜索', running: '正在搜索', success: '搜索完成', error: '搜索失败', cancelled: '已停止，可继续',
}
const targetText: Record<BatchSearchRequest['target'], string> = { local: '自建库', global: '现网 Global', auto: '业务搜图' }

type JobOutcome = { response: SearchResponse; elapsed: number } | { error: string; elapsed: number }

/** Search one group of jobs (one request): the outcome of each, by job id. A failed request throws. */
async function searchGroup(group: readonly SearchJob[], request: BatchSearchRequest, baseUrl: string, signal: AbortSignal): Promise<Map<string, JobOutcome>> {
  const started = performance.now()
  const seconds = () => (performance.now() - started) / 1000
  const outcomes = new Map<string, JobOutcome>()
  if (request.target === 'local') {
    const batch = await searchLocalBatch({
      files: group.map(job => job.file), baseUrl, signal, scene: request.scene, scoreThreshold: request.scoreThreshold,
    })
    const share = seconds() / group.length
    group.forEach((job, index) => {
      const outcome = batch.outcomes.find(entry => entry.index === index)
      const elapsed = outcome?.elapsed ?? share
      outcomes.set(job.id, outcome?.response
        ? { response: outcome.response, elapsed }
        : { error: outcome?.error ?? '服务端没有返回这张图片的结果，请重试。', elapsed })
    })
    return outcomes
  }
  const job = group[0]
  const response = request.target === 'global'
    ? await searchGlobalImages({ file: job.file, baseUrl, signal, searchNum: request.searchNum })
    : await searchAutoImages({
      file: job.file, baseUrl, signal, scene: request.scene, scoreThreshold: request.scoreThreshold,
      topK: request.topK, globalFallback: request.globalFallback,
    })
  outcomes.set(job.id, { response, elapsed: seconds() })
  return outcomes
}

function grouped(jobs: readonly SearchJob[], size: number): SearchJob[][] {
  const groups: SearchJob[][] = []
  for (let start = 0; start < jobs.length; start += size) groups.push(jobs.slice(start, start + size))
  return groups
}

/** Keep mounted (hide its wrapper) when switching query modes to preserve queued and cached results. */
export function BatchSearchPanel({ baseUrl, request, blocked = null, active: isActive = true, onResult, onClearResult, onRunningChange }: BatchSearchPanelProps) {
  const [jobs, setJobs] = useState<SearchJob[]>([])
  const [running, setRunning] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  const selectedByUser = useRef(false)
  const displayedResult = useRef<{ id: string; response: SearchResponse } | null>(null)
  const activeMode = useRef(isActive)
  const jobsRef = useRef<SearchJob[]>([])
  const mounted = useRef(true)
  const runId = useRef(0)
  const active = useRef<{ controller: AbortController; timer: number } | null>(null)
  const isRunning = useRef(false)
  const nextId = useRef(0)
  const baseRef = useRef(baseUrl)
  const requestRef = useRef(request)
  const blockedRef = useRef(blocked)
  const previousBase = useRef(baseUrl)
  const callbacks = useRef({ onResult, onClearResult, onRunningChange })
  activeMode.current = isActive
  baseRef.current = baseUrl
  requestRef.current = request
  blockedRef.current = blocked
  callbacks.current = { onResult, onClearResult, onRunningChange }

  const commit = useCallback((next: SearchJob[]) => {
    jobsRef.current = next
    if (mounted.current) setJobs(next)
  }, [])

  const publishJob = useCallback((job: SearchJob) => {
    if (!activeMode.current) return
    if (job.response && job.elapsed !== undefined) {
      if (displayedResult.current?.id === job.id && displayedResult.current.response === job.response) return
      callbacks.current.onResult({ response: job.response, file: job.file, elapsed: job.elapsed })
      displayedResult.current = { id: job.id, response: job.response }
    } else {
      callbacks.current.onClearResult?.()
      displayedResult.current = null
    }
  }, [])

  useEffect(() => {
    displayedResult.current = null
    if (!isActive) return
    const selected = jobsRef.current.find(job => job.id === selectedIdRef.current)
    if (selected) publishJob(selected)
  }, [isActive, publishJob])

  const stop = useCallback(() => {
    runId.current++
    if (active.current) {
      window.clearTimeout(active.current.timer)
      active.current.controller.abort('cancelled')
      active.current = null
    }
    commit(jobsRef.current.map(job => job.status === 'running' ? { ...job, status: 'cancelled', error: undefined } : job))
    if (isRunning.current) {
      isRunning.current = false
      if (mounted.current) {
        setRunning(false)
        callbacks.current.onRunningChange?.(false)
      }
    }
  }, [commit])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      runId.current++
      if (active.current) {
        window.clearTimeout(active.current.timer)
        active.current.controller.abort('unmounted')
        active.current = null
      }
      isRunning.current = false
      jobsRef.current.forEach(job => URL.revokeObjectURL(job.preview))
    }
  }, [])

  useEffect(() => {
    if (previousBase.current !== baseUrl) {
      previousBase.current = baseUrl
      if (isRunning.current) {
        stop()
        notify.warn('搜索服务地址已改变，当前搜索已停止。可继续未完成的图片。', { title: '批量搜索已停止' })
      }
    }
  }, [baseUrl, stop])

  function addFiles(incoming: File[]) {
    if (isRunning.current) return
    const selected = selectBatchFiles(incoming, jobsRef.current.map(job => job.file))
    if (selected.messages.length) notify.warn(selected.files.length ? `已加入 ${selected.files.length} 张，有些没有加入：` : '这些图片没有加入：', { title: '部分图片未加入', details: selected.messages })
    const next = [...jobsRef.current, ...selected.files.map(file => ({
      id: `batch-query-${++nextId.current}`, file,
      preview: URL.createObjectURL(file), status: 'pending' as const,
    }))]
    commit(next)
    if (!selectedIdRef.current && next.length) selectJob(next[0], false)
  }

  function clearQueue() {
    if (isRunning.current) return
    jobsRef.current.forEach(job => URL.revokeObjectURL(job.preview))
    commit([])
    selectedIdRef.current = null
    selectedByUser.current = false
    displayedResult.current = null
    setSelectedId(null)
    callbacks.current.onClearResult?.()
  }

  function removeJob(id: string) {
    if (isRunning.current) return
    const index = jobsRef.current.findIndex(value => value.id === id)
    const job = jobsRef.current[index]
    if (job) URL.revokeObjectURL(job.preview)
    const next = jobsRef.current.filter(value => value.id !== id)
    commit(next)
    if (selectedIdRef.current === id) {
      const adjacent = next[Math.min(index, next.length - 1)]
      if (adjacent) selectJob(adjacent)
      else {
        selectedIdRef.current = null
        selectedByUser.current = false
        displayedResult.current = null
        setSelectedId(null)
        callbacks.current.onClearResult?.()
      }
    }
  }

  function selectJob(job: SearchJob, manual = true) {
    selectedIdRef.current = job.id
    selectedByUser.current = manual
    setSelectedId(job.id)
    publishJob(job)
  }

  function showResult(job: SearchJob) {
    if (!job.response || job.elapsed === undefined) return
    selectJob(job)
  }

  async function start(retryFailed = false) {
    if (isRunning.current) return
    const targets = jobsRef.current.filter(job => retryFailed
      ? job.status === 'error' || job.status === 'cancelled'
      : job.status === 'pending' || job.status === 'cancelled')
    if (!targets.length) return
    if (blockedRef.current) {
      notify.warn(blockedRef.current, { title: '暂时不能搜索' })
      return
    }
    const token = ++runId.current
    const requestBase = baseRef.current
    const runRequest = requestRef.current
    let autoShown = false
    isRunning.current = true
    setRunning(true)
    const currentRun = () => mounted.current && runId.current === token && baseRef.current === requestBase
    try {
      displayedResult.current = null
      callbacks.current.onRunningChange?.(true)
      const selected = jobsRef.current.find(job => job.id === selectedIdRef.current)
      if (selected?.response && selected.elapsed !== undefined) publishJob(selected)
      for (const group of grouped(targets, runRequest.target === 'local' ? LOCAL_BATCH_SIZE : 1)) {
        if (!currentRun()) break
        const ids = new Set(group.map(job => job.id))
        const controller = new AbortController()
        const timer = window.setTimeout(() => controller.abort('timeout'), REQUEST_TIMEOUT_MS)
        active.current = { controller, timer }
        commit(jobsRef.current.map(job => ids.has(job.id) ? { ...job, status: 'running', error: undefined } : job))
        const started = performance.now()
        const completed: SearchJob[] = []
        try {
          const outcomes = await searchGroup(group, runRequest, requestBase, controller.signal)
          if (!currentRun()) break
          if (controller.signal.aborted) throw new Error('搜索超时')
          commit(jobsRef.current.map((job): SearchJob => {
            const outcome = outcomes.get(job.id)
            if (!outcome) return job
            if ('response' in outcome) {
              const done: SearchJob = { ...job, status: 'success', response: outcome.response, elapsed: outcome.elapsed, error: undefined, via: runRequest.target }
              completed.push(done)
              return done
            }
            return { ...job, status: 'error', error: outcome.error, elapsed: outcome.elapsed, response: undefined, via: runRequest.target }
          }))
        } catch (cause) {
          if (!currentRun()) break
          const error = controller.signal.aborted && controller.signal.reason === 'timeout'
            ? `搜索超过 ${REQUEST_TIMEOUT_MS / 1000} 秒，请稍后重试。`
            : cause instanceof Error ? cause.message : '搜索失败，请重试。'
          const elapsed = (performance.now() - started) / 1000
          commit(jobsRef.current.map(job => ids.has(job.id)
            ? { ...job, status: 'error', error, elapsed, via: runRequest.target }
            : job))
        } finally {
          window.clearTimeout(timer)
          if (active.current?.controller === controller) active.current = null
        }
        // Callback exceptions must not turn a completed API query into a failed job.
        if (currentRun()) {
          for (const done of completed) {
            if (selectedIdRef.current === done.id || (!autoShown && !selectedByUser.current)) {
              autoShown = true
              const manual = selectedByUser.current
              selectJob(done, manual)
            }
          }
        }
      }
    } finally {
      if (mounted.current && runId.current === token) {
        isRunning.current = false
        setRunning(false)
        callbacks.current.onRunningChange?.(false)
      }
    }
  }

  /** Search every image again with what is set now (another target, scene or threshold). */
  function restart() {
    if (isRunning.current || !jobsRef.current.length) return
    if (blockedRef.current) {
      notify.warn(blockedRef.current, { title: '暂时不能搜索' })
      return
    }
    commit(jobsRef.current.map(({ id, file, preview }) => ({ id, file, preview, status: 'pending' as const })))
    displayedResult.current = null
    callbacks.current.onClearResult?.()
    void start()
  }

  const done = jobs.filter(job => job.status === 'success' || job.status === 'error').length
  const success = jobs.filter(job => job.status === 'success').length
  const failures = jobs.filter(job => job.status === 'error').length
  const resumable = jobs.some(job => job.status === 'pending' || job.status === 'cancelled')
  const retryable = jobs.some(job => job.status === 'error' || job.status === 'cancelled')
  const hasStarted = jobs.some(job => job.status !== 'pending')
  const selectedIndex = jobs.findIndex(job => job.id === selectedId)
  const selectedJob = jobs[selectedIndex]

  return <section className="batch-search-panel" aria-label="批量图像搜索">
    <div className="batch-controls">
      <div className={`batch-upload ${dragging ? 'is-dragging' : ''}`} onDragOver={event => {
        event.preventDefault()
        if (!running) setDragging(true)
      }} onDragLeave={() => setDragging(false)} onDrop={event => {
        event.preventDefault(); setDragging(false)
        addFiles(Array.from(event.dataTransfer.files))
      }}>
        <ImageFilePicker className="batch-upload-trigger" label="选择批量查询图片" multiple disabled={running || jobs.length >= maxBatchImages} onFiles={addFiles}>
          <span className="batch-upload-icon"><UploadCloud size={22} strokeWidth={1.5} /></span>
          <span className="batch-upload-copy">
            <b>{jobs.length ? '继续添加查询图片' : '选择或拖入一批图片'}</b>
            <span>最多 {maxBatchImages} 张 · 每张 ≤15 MB · JPG / PNG / WebP / BMP</span>
          </span>
        </ImageFilePicker>
      </div>
      <div className="batch-run-actions">
        {running
          ? <button type="button" className="btn btn-secondary" onClick={stop}><Square size={15} />停止批量搜索</button>
          : <button type="button" className="btn btn-primary" disabled={!resumable} onClick={() => void start()}><Play size={15} />{hasStarted && resumable ? '继续搜索' : '开始批量搜索'}</button>}
        {!running && retryable && <button type="button" className="btn btn-secondary" onClick={() => void start(true)}><RotateCcw size={15} />重试未完成</button>}
        {!running && done > 0 && <button type="button" className="btn btn-secondary" title={`按当前设置（${targetText[request.target]}）把队列里的图片全部重新搜索一遍`} onClick={restart}><RefreshCw size={15} />全部重新搜索</button>}
      </div>
    </div>
    {selectedJob && <figure className="batch-selected-preview">
      <div className="batch-selected-heading"><b>当前查询图</b><span>{selectedIndex + 1} / {jobs.length}</span>
        <button type="button" className="icon-button" aria-label="上一张查询图片" disabled={selectedIndex <= 0} onClick={() => selectJob(jobs[selectedIndex - 1])}><ChevronLeft size={16} /></button>
        <button type="button" className="icon-button" aria-label="下一张查询图片" disabled={selectedIndex >= jobs.length - 1} onClick={() => selectJob(jobs[selectedIndex + 1])}><ChevronRight size={16} /></button>
      </div>
      <img className="batch-selected-image" src={selectedJob.preview} alt={`当前查询图片：${selectedJob.file.name}`} />
      <figcaption title={selectedJob.file.name}>{selectedJob.file.name}</figcaption>
    </figure>}
    {jobs.length > 0 ? <>
      <div className="batch-progress" aria-live="polite">
        <div><b>{running ? '正在处理' : done === jobs.length ? '批量搜索完成' : '搜索队列'}</b><span>{done} / {jobs.length}</span></div>
        <progress aria-label="批量搜索进度" value={done} max={jobs.length} />
        <p>成功 {success} 张{failures > 0 ? ` · 失败 ${failures} 张` : ''}{jobs.some(job => job.status === 'cancelled') ? ' · 可继续已停止任务' : ''}</p>
      </div>
      <div className="batch-queue-heading"><h3><Images size={15} />查询图片 <span>{jobs.length}</span></h3><button className="text-button" type="button" disabled={running} onClick={clearQueue}><Trash2 size={13} />清空队列</button></div>
      <ul className="batch-queue" aria-label="批量搜索队列">{jobs.map((job, index) => <li key={job.id} className={`batch-job batch-job-${job.status} ${selectedId === job.id ? 'is-viewed' : ''}`}>
        <button type="button" className="batch-job-preview-button" aria-label={`预览查询图片：${job.file.name}`} aria-pressed={selectedId === job.id} onClick={() => selectJob(job)}>
          <img src={job.preview} alt="" className="batch-job-preview" onError={event => { event.currentTarget.style.visibility = 'hidden' }} />
        </button>
        <div className="batch-job-info"><b title={job.file.name}>{index + 1}. {job.file.name}</b>
          <span className="batch-job-status">{job.status === 'running' ? <LoaderCircle size={13} className="spin" /> : job.status === 'success' ? <CheckCircle2 size={13} /> : job.status === 'error' ? <XCircle size={13} /> : job.status === 'cancelled' ? <Clock3 size={13} /> : <Circle size={12} />}{statusText[job.status]}</span>
          {job.response && <span className="batch-job-meta">{job.via ? `${targetText[job.via]} · ` : ''}{job.response.items.length} 个结果 · {job.elapsed?.toFixed(1)} 秒</span>}
          {job.error && <p className="batch-job-error">{job.error}</p>}
          {job.response?.message && <p className="batch-job-message">{job.response.message}</p>}
          {job.response && <button type="button" className="text-button batch-view-result" aria-label={`查看结果：${job.file.name}`} aria-pressed={selectedId === job.id} onClick={() => showResult(job)}>{selectedId === job.id ? '正在查看' : '查看结果'}</button>}
        </div>
        {!running && <button type="button" className="icon-button batch-remove" aria-label={`移除查询图片：${job.file.name}`} onClick={() => removeJob(job.id)}><X size={14} /></button>}
      </li>)}</ul>
    </> : <p className="batch-empty"><Images size={14} />逐张搜索，首张完成后自动展示结果，可切换查看已完成图片。</p>}
  </section>
}
