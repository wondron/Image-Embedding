import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, Circle, Clock3, Images, LoaderCircle, Play, RotateCcw, Square, Trash2, UploadCloud, X, XCircle } from 'lucide-react'
import { searchImages } from '../lib/search'
import { maxBatchImages, selectBatchFiles } from '../lib/batch-search'
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
  onResult: (payload: BatchSearchResult) => void
  onRunningChange?: (running: boolean) => void
}

const statusText: Record<JobStatus, string> = {
  pending: '等待搜索', running: '正在搜索', success: '搜索完成', error: '搜索失败', cancelled: '已停止，可继续',
}

/** Keep mounted (hide its wrapper) when switching query modes to preserve queued and cached results. */
export function BatchSearchPanel({ baseUrl, onResult, onRunningChange }: BatchSearchPanelProps) {
  const [jobs, setJobs] = useState<SearchJob[]>([])
  const [running, setRunning] = useState(false)
  const [messages, setMessages] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [viewedId, setViewedId] = useState<string | null>(null)
  const jobsRef = useRef<SearchJob[]>([])
  const mounted = useRef(true)
  const runId = useRef(0)
  const active = useRef<{ controller: AbortController; jobId: string; timer: number } | null>(null)
  const isRunning = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  const nextId = useRef(0)
  const baseRef = useRef(baseUrl)
  const previousBase = useRef(baseUrl)
  const callbacks = useRef({ onResult, onRunningChange })
  baseRef.current = baseUrl
  callbacks.current = { onResult, onRunningChange }

  const commit = useCallback((next: SearchJob[]) => {
    jobsRef.current = next
    if (mounted.current) setJobs(next)
  }, [])

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
    commit([...jobsRef.current, ...selected.files.map(file => ({
      id: `batch-query-${++nextId.current}`, file,
      preview: URL.createObjectURL(file), status: 'pending' as const,
    }))])
  }

  function clearQueue() {
    if (isRunning.current) return
    jobsRef.current.forEach(job => URL.revokeObjectURL(job.preview))
    commit([])
    setViewedId(null)
    setMessages([])
  }

  function removeJob(id: string) {
    if (isRunning.current) return
    const job = jobsRef.current.find(value => value.id === id)
    if (job) URL.revokeObjectURL(job.preview)
    commit(jobsRef.current.filter(value => value.id !== id))
    if (viewedId === id) setViewedId(null)
  }

  function showResult(job: SearchJob) {
    if (!job.response || job.elapsed === undefined) return
    setViewedId(job.id)
    callbacks.current.onResult({ response: job.response, file: job.file, elapsed: job.elapsed })
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
    callbacks.current.onRunningChange?.(true)
    const currentRun = () => mounted.current && runId.current === token && baseRef.current === requestBase
    try {
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
        if (completed && !autoShown && currentRun()) {
          autoShown = true
          showResult(completed)
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

  return <section className="batch-search-panel" aria-label="批量图像搜索">
    <input ref={input} type="file" multiple accept="image/jpeg,image/png,image/webp,image/bmp" className="sr-only" aria-label="选择批量查询图片" disabled={running} onChange={event => {
      addFiles(Array.from(event.target.files ?? []))
      event.target.value = ''
    }} />
    <div className={`batch-upload ${dragging ? 'is-dragging' : ''}`} onDragOver={event => {
      event.preventDefault()
      if (!running) setDragging(true)
    }} onDragLeave={() => setDragging(false)} onDrop={event => {
      event.preventDefault(); setDragging(false)
      addFiles(Array.from(event.dataTransfer.files))
    }}>
      <button type="button" disabled={running || jobs.length >= maxBatchImages} onClick={() => input.current?.click()}>
        <span className="batch-upload-icon"><UploadCloud size={26} strokeWidth={1.5} /></span>
        <b>{jobs.length ? '继续添加查询图片' : '选择或拖入一批图片'}</b>
        <span>最多 30 张 · 每张不超过 15 MB</span>
        <span>JPG / PNG / WebP / BMP</span>
      </button>
    </div>
    {messages.length > 0 && <div className="batch-messages" role="status">{messages.map((message, index) => <p key={index}>{message}</p>)}</div>}
    <div className="batch-run-actions">
      {running
        ? <button type="button" className="btn btn-secondary" onClick={stop}><Square size={15} />停止批量搜索</button>
        : <button type="button" className="btn btn-primary" disabled={!resumable} onClick={() => void start()}><Play size={15} />{hasStarted && resumable ? '继续搜索' : '开始批量搜索'}</button>}
      {!running && retryable && <button type="button" className="btn btn-secondary" onClick={() => void start(true)}><RotateCcw size={15} />重试未完成</button>}
    </div>
    <p className="batch-explanation">图片将逐张搜索。首张完成后自动展示结果，也可随时切换查看已完成图片。</p>
    {jobs.length > 0 ? <>
      <div className="batch-progress" aria-live="polite">
        <div><b>{running ? '正在处理' : done === jobs.length ? '批量搜索完成' : '搜索队列'}</b><span>{done} / {jobs.length}</span></div>
        <progress aria-label="批量搜索进度" value={done} max={jobs.length} />
        <p>成功 {success} 张{failures > 0 ? ` · 失败 ${failures} 张` : ''}{jobs.some(job => job.status === 'cancelled') ? ' · 可继续已停止任务' : ''}</p>
      </div>
      <div className="batch-queue-heading"><h3><Images size={15} />查询图片 <span>{jobs.length}</span></h3><button className="text-button" type="button" disabled={running} onClick={clearQueue}><Trash2 size={13} />清空队列</button></div>
      <ul className="batch-queue" aria-label="批量搜索队列">{jobs.map((job, index) => <li key={job.id} className={`batch-job batch-job-${job.status} ${viewedId === job.id ? 'is-viewed' : ''}`}>
        <img src={job.preview} alt="" className="batch-job-preview" onError={event => { event.currentTarget.style.visibility = 'hidden' }} />
        <div className="batch-job-info"><b title={job.file.name}>{index + 1}. {job.file.name}</b>
          <span className="batch-job-status">{job.status === 'running' ? <LoaderCircle size={13} className="spin" /> : job.status === 'success' ? <CheckCircle2 size={13} /> : job.status === 'error' ? <XCircle size={13} /> : job.status === 'cancelled' ? <Clock3 size={13} /> : <Circle size={12} />}{statusText[job.status]}</span>
          {job.response && <span className="batch-job-meta">{job.response.items.length} 个结果 · {job.elapsed?.toFixed(1)} 秒</span>}
          {job.error && <p className="batch-job-error">{job.error}</p>}
          {job.response?.message && <p className="batch-job-message">{job.response.message}</p>}
          {job.response && <button type="button" className="text-button batch-view-result" aria-label={`查看结果：${job.file.name}`} aria-pressed={viewedId === job.id} onClick={() => showResult(job)}>{viewedId === job.id ? '正在查看' : '查看结果'}</button>}
        </div>
        {!running && <button type="button" className="icon-button batch-remove" aria-label={`移除查询图片：${job.file.name}`} onClick={() => removeJob(job.id)}><X size={14} /></button>}
      </li>)}</ul>
    </> : <p className="batch-empty"><Images size={16} />添加多张图片，分别发现相似结果。</p>}
  </section>
}
