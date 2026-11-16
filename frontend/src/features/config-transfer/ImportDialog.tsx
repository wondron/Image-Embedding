import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileJson, LoaderCircle } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { ApiError } from '../../shared/api'
import {
  IMPORT_ACTION_TEXT,
  IMPORT_KIND_TEXT,
  importChangeCount,
  importChangeText,
  type ImportReport,
  type ImportRow,
} from './model'
import './config-transfer.css'

export interface ImportDialogProps {
  /** 「上传类别树」 / 「上传场景配置」. */
  title: string
  fileName: string
  /** Run the import: a preview (dryRun) or for real. prune is only passed on when prunable. */
  run: (options: { dryRun: boolean; prune: boolean }) => Promise<ImportReport>
  /** Scenes: offer to delete the rules and labels the file does not list. */
  prunable?: boolean
  /** What the stored items the file does not list are, e.g. 类别 / 场景. */
  leftoverNoun: string
  /**
   * After a real import was sent: the caller reloads its data. The report may list items that were
   * not imported; null when the request failed, so what was written before the failure is unknown.
   */
  onApplied: (report: ImportReport | null) => void
  onClose: () => void
}

type Phase = 'previewing' | 'preview' | 'applying' | 'done'

function failureText(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.traceId ? ` · 追踪号 ${error.traceId}` : ''}`
  if (error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError')) return '等待超时，请稍后重试。'
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

function RowContent({ row }: { row: ImportRow }) {
  if (row.action === 'error') {
    return <span className="cx-row-error">{row.detail ?? '没有导入'}{row.error ? <code>{row.error}</code> : null}</span>
  }
  if (row.action === 'delete') return <span className="cx-row-muted">库里有、文件里没有列出</span>
  if (!row.changes.length) return <span className="cx-row-muted">—</span>
  return <ul className="cx-changes">{row.changes.map((change, index) => <li key={`${change.field}-${index}`}>{importChangeText(change)}</li>)}</ul>
}

/**
 * Upload of a configuration file in two steps: a preview (the server's dry run: what would be
 * created, updated, deleted, and what cannot be imported), then the import itself, whose report is
 * shown the same way. Nothing is written before 「确认导入」.
 */
export function ImportDialog({ title, fileName, run, prunable = false, leftoverNoun, onApplied, onClose }: ImportDialogProps) {
  const [phase, setPhase] = useState<Phase>('previewing')
  const [report, setReport] = useState<ImportReport | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [prune, setPrune] = useState(false)
  const generation = useRef(0)
  const mounted = useRef(true)
  const runRef = useRef(run)
  runRef.current = run

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  // The preview follows the prune switch: deletions are shown before anything is confirmed
  useEffect(() => {
    const token = ++generation.current
    setPhase('previewing')
    setFailure(null)
    runRef.current({ dryRun: true, prune }).then(
      next => {
        if (!mounted.current || token !== generation.current) return
        setReport(next)
        setPhase('preview')
      },
      error => {
        if (!mounted.current || token !== generation.current) return
        setReport(null)
        setFailure(failureText(error))
        setPhase('preview')
      },
    )
  }, [prune])

  async function apply() {
    if (phase !== 'preview' || !report) return
    const token = ++generation.current
    setPhase('applying')
    setFailure(null)
    let result: ImportReport | null = null
    let problem: string | null = null
    try {
      result = await runRef.current({ dryRun: false, prune })
    } catch (error) {
      // Some items may have been written before the failure: the preview is no longer what is stored
      problem = `${failureText(error)} 已经写入的条目不会回退；重新上传同一个文件会从剩下的继续。`
    }
    if (!mounted.current || token !== generation.current) return
    setReport(result)
    setFailure(problem)
    setPhase('done')
    onApplied(result)
  }

  const summary = report?.summary
  const changes = summary ? importChangeCount(summary) : 0
  const applied = phase === 'done' && report !== null && !report.dryRun
  const working = phase === 'previewing' || phase === 'applying'

  return <Dialog
    title={title}
    size="lg"
    onClose={onClose}
    dismissDisabled={phase === 'applying'}
    footer={<div className="cx-footer">
      {phase === 'done'
        ? <button type="button" className="mg-btn mg-primary" onClick={onClose}>完成</button>
        : <>
          <span className="cx-footer-note">{phase === 'applying' ? '正在导入，请不要关闭页面…' : '确认之前不会写入任何内容。'}</span>
          <button type="button" className="mg-btn mg-secondary" disabled={phase === 'applying'} onClick={onClose}>取消</button>
          <button type="button" className="mg-btn mg-primary" disabled={working || !report || changes === 0} onClick={() => void apply()}>
            {phase === 'applying' ? <><LoaderCircle size={15} className="spin" />正在导入…</> : `确认导入${changes ? `（${changes} 项）` : ''}`}
          </button>
        </>}
    </div>}
  >
    <div className="cx-dialog">
      <p className="cx-file"><FileJson size={16} aria-hidden /><span title={fileName}>{fileName}</span><em>{applied ? '导入结果' : '导入预览'}</em></p>

      {prunable && <label className={`cx-prune${prune ? ' is-on' : ''}`}>
        <input type="checkbox" checked={prune} disabled={phase !== 'preview'} onChange={event => setPrune(event.target.checked)} />
        <span><b>同时删除文件里没有列出的规则和场景标签</b><small>只作用于文件里出现的场景，按文件完全覆盖它们；场景本身从不删除。不勾选时只新建和更新。</small></span>
      </label>}

      {phase === 'previewing' && <p className="cx-status" role="status"><LoaderCircle size={15} className="spin" />正在对照库里的内容生成预览…</p>}
      {failure && <div className="mg-errors cx-failure" role="alert"><div><strong>{phase === 'done' ? '导入没有完成' : '没能生成预览'}</strong></div><p>{failure}</p></div>}

      {summary && <>
        <div className="cx-summary" role="status" aria-label="导入统计">
          <span className="cx-stat is-create"><b>{summary.create}</b>新建</span>
          <span className="cx-stat is-update"><b>{summary.update}</b>更新</span>
          {(prunable || summary.delete > 0) && <span className="cx-stat is-delete"><b>{summary.delete}</b>删除</span>}
          <span className="cx-stat"><b>{summary.unchanged}</b>无变化</span>
          <span className={`cx-stat${summary.error ? ' is-error' : ''}`}><b>{summary.error}</b>有问题</span>
          <span className="cx-stat-total">文件共 {summary.total} 项</span>
        </div>

        {applied
          ? <p className={`cx-note${summary.error ? ' is-warn' : ' is-ok'}`}>
            {summary.error ? <AlertTriangle size={15} aria-hidden /> : <CheckCircle2 size={15} aria-hidden />}
            {summary.error
              ? `已导入 ${changes} 项，另有 ${summary.error} 项没有导入（见下表）。改好文件后重新上传即可，已导入的不会重复。`
              : changes ? `已导入 ${changes} 项。` : '库里的内容和文件一致，没有需要导入的。'}
          </p>
          : phase === 'preview' && <p className={`cx-note${summary.error ? ' is-warn' : ''}`}>
            {summary.error > 0 && <AlertTriangle size={15} aria-hidden />}
            {changes === 0
              ? summary.error ? `没有可以导入的内容：${summary.error} 项有问题（见下表）。` : '库里的内容和文件一致，没有需要导入的。'
              : summary.error ? `有问题的 ${summary.error} 项不会导入，其余 ${changes} 项照常导入。` : `确认后将写入 ${changes} 项。`}
          </p>}

        {applied && report?.syncRequired && <p className="cx-note is-warn"><AlertTriangle size={15} aria-hidden />这些改动要「全量同步」后才作用到已入库的图片，检索也是。</p>}
        {!applied && phase === 'preview' && report?.syncRequired && changes > 0 && <p className="cx-note">导入后需要「全量同步」，已入库的图片才按新配置归属。</p>}
        {applied && report?.sceneSync && <p className={`cx-note${report.sceneSync.pending ? ' is-warn' : ''}`}>
          菜系改动后重算了 {report.sceneSync.images} 张图片的场景归属，{report.sceneSync.changed} 张有变化，
          {report.sceneSync.pending ? `其中 ${report.sceneSync.pending} 张还没同步到向量库（后台对账或全量同步会补上）。` : '已全部同步到向量库。'}
        </p>}

        {report && report.rows.length > 0 && <div className="cx-table-scroll">
          <table className="cx-table">
            <thead><tr><th>类型</th><th>对象</th><th>操作</th><th>内容</th></tr></thead>
            <tbody>{report.rows.map((row, index) => <tr key={`${row.kind}-${row.key}-${index}`} className={`is-${row.action}`}>
              <td>{IMPORT_KIND_TEXT[row.kind]}</td>
              <td className="cx-object">
                {row.name && <b>{row.name}</b>}
                <code title={row.where ?? undefined}>{row.key}</code>
              </td>
              <td><span className={`cx-action is-${row.action}`}>{IMPORT_ACTION_TEXT[row.action]}</span></td>
              <td><RowContent row={row} /></td>
            </tr>)}</tbody>
          </table>
        </div>}
        {report?.rowsTruncated && <p className="cx-note">变更太多，上表只列出了前 {report.rows.length} 行（有问题的优先）；统计数字是完整的。</p>}

        {summary.notInFile > 0 && <details className="cx-leftovers">
          <summary>库里另有 {summary.notInFile} 个{leftoverNoun}不在文件里，导入不会改动它们</summary>
          <ul>{report?.notInFile.map(entry => <li key={entry.id}>{entry.name ? `${entry.name} ` : ''}<code>{entry.code}</code></li>)}</ul>
          {report && summary.notInFile > report.notInFile.length && <p>只列出前 {report.notInFile.length} 个。</p>}
        </details>}
      </>}
    </div>
  </Dialog>
}
