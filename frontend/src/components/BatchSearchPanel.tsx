import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, Circle, Clock3, Images, LoaderCircle, Play, RotateCcw, Square, Trash2, UploadCloud, X, XCircle } from 'lucide-react'
import { searchImages } from '../lib/search'
import { maxBatchImages, selectBatchFiles } from '../lib/batch-search'
import { ImageFilePicker } from './ImageFilePicker'
import type { SearchResponse } from '../types'
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
}

export interface BatchSearchResult {
  response: SearchResponse
  file: File
  /** Elapsed seconds for this query, excluding other queued images. */
  elapsed: number
}

interface BatchSearchPanelProps {
  baseUrl: string
  active?: boolean
  onResult: (payload: BatchSearchResult) => void
  onClearResult?: () => void
  onRunningChange?: (running: boolean) => void
}

const statusText: Record<JobStatus, string> = {
  pending: '等待搜索', running: '正在搜索', success: '搜索完成', error: '搜索失败', cancelled: '已停止，可继续',
}

/** Keep mounted (hide its wrapper) when switching query modes to preserve queued and cached results. */
export function BatchSearchPanel({ baseUrl, active: isActive = true, onResult, onClearResult, onRunningChange }: BatchSearchPanelProps) {
  const [jobs, setJobs] = useState<SearchJob[]>([])
  const [running, setRunning] = useState(false)
  const [messages, setMessages] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  const selectedByUser = useRef(false)
  const displayedResult = useRef<{ id: string; response: SearchResponse } | null>(null)
  const activeMode = useRef(isActive)
  const jobsRef = useRef<SearchJob[]>([])
  const mounted = useRef(true)
  const runId = useRef(0)
  const active = useRef<{ controller: AbortController; jobId: string; timer: number } | null>(null)
  const isRunning = useRef(false)
  const nextId = useRef(0)
  const baseRef = useRef(baseUrl)
  const previousBase = useRef(baseUrl)
  const callbacks = useRef({ onResult, onClearResult, onRunningChange })
  activeMode.current = isActive
  baseRef.current = baseUrl
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
        setMessages(['搜索服务地址已改变，当前搜索已停止。可继续未完成的图片。'])
      }
    }
  }, [baseUrl, stop])

  function addFiles(incoming: File[]) {
    if (isRunning.current) return
    const selected = selectBatchFiles(incoming, jobsRef.current.map(job => job.file))
    setMessages(selected.messages)
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
    setMessages([])
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
    const token = ++runId.current
    const requestBase = baseRef.current
    let autoShown = false
    isRunning.current = true
    setRunning(true)
    setMessages([])
    const currentRun = () => mounted.current && runId.current === token && baseRef.current === requestBase
    try {
      displayedResult.current = null
      callbacks.current.onRunningChange?.(true)
      const selected = jobsRef.current.find(job => job.id === selectedIdRef.current)
      if (selected?.response && selected.elapsed !== undefined) publishJob(selected)
      for (const target of targets) {
        if (!currentRun()) break
        const controller = new AbortController()
        const timer = window.setTimeout(() => controller.abort('timeout'), 120_000)
        active.current = { controller, jobId: target.id, timer }
        commit(jobsRef.current.map(job => job.id === target.id ? { ...job, status: 'running', error: undefined } : job))
        const started = performance.now()
        let completed: SearchJob | null = null
        try {
          const response = await searchImages({ file: target.file, baseUrl: requestBase, signal: controller.signal })
          if (!currentRun()) break
          if (controller.signal.aborted) throw new Error('搜索超时')
          completed = { ...target, status: 'success', response, elapsed: (performance.now() - started) / 1000, error: undefined }
          commit(jobsRef.current.map(job => job.id === target.id ? completed! : job))
        } catch (cause) {
          if (!currentRun()) break
          const error = controller.signal.aborted && controller.signal.reason === 'timeout'
            ? '搜索超过 120 秒，请稍后重试。'
            : cause instanceof Error ? cause.message : '搜索失败，请重试。'
          commit(jobsRef.current.map(job => job.id === target.id
            ? { ...job, status: 'error', error, elapsed: (performance.now() - started) / 1000 }
            : job))
        } finally {
          window.clearTimeout(timer)
          if (active.current?.controller === controller) active.current = null
        }
        // Callback exceptions must not turn a completed API query into a failed job.
        if (completed && currentRun()) {
          if (selectedIdRef.current === completed.id || (!autoShown && !selectedByUser.current)) {
            autoShown = true
            const manual = selectedByUser.current
            selectJob(completed, manual)
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
      </div>
    </div>
    {messages.length > 0 && <div className="batch-messages" role="status">{messages.map((message, index) => <p key={index}>{message}</p>)}</div>}
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
          {job.response && <span className="batch-job-meta">{job.response.items.length} 个结果 · {job.elapsed?.toFixed(1)} 秒</span>}
          {job.error && <p className="batch-job-error">{job.error}</p>}
          {job.response?.message && <p className="batch-job-message">{job.response.message}</p>}
          {job.response && <button type="button" className="text-button batch-view-result" aria-label={`查看结果：${job.file.name}`} aria-pressed={selectedId === job.id} onClick={() => showResult(job)}>{selectedId === job.id ? '正在查看' : '查看结果'}</button>}
        </div>
        {!running && <button type="button" className="icon-button batch-remove" aria-label={`移除查询图片：${job.file.name}`} onClick={() => removeJob(job.id)}><X size={14} /></button>}
      </li>)}</ul>
    </> : <p className="batch-empty"><Images size={14} />逐张搜索，首张完成后自动展示结果，可切换查看已完成图片。</p>}
  </section>
}
