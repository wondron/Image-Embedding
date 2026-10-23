import { useEffect, useRef, useState } from 'react'
import { Archive, CheckCircle2, Download, ExternalLink, FolderOpen, LoaderCircle, RotateCcw, Square, TriangleAlert } from 'lucide-react'
import type { SearchImage } from '../types'
import { Dialog } from './Dialog'
import { safeHttpUrl } from '../lib/search'
import { canChooseDownloadDirectory, chooseDownloadDirectory, downloadImages,
  type DownloadDirectory, type DownloadProgress, type DownloadReport } from '../lib/downloads'
import './DownloadDialog.css'

export function DownloadDialog({ items, onClose, apiBase }: { items: SearchImage[]; onClose: () => void; apiBase?: string }) {
  const [mode, setMode] = useState<'zip' | 'folder'>('zip')
  const [directory, setDirectory] = useState<DownloadDirectory | null>(null)
  const [progress, setProgress] = useState<DownloadProgress | null>(null)
  const [report, setReport] = useState<DownloadReport | null>(null)
  const [runMode, setRunMode] = useState<'zip' | 'folder'>('zip')
  const [busy, setBusy] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const [message, setMessage] = useState('')
  const active = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  const folderSupported = canChooseDownloadDirectory()
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; active.current?.abort() }
  }, [])

  async function chooseFolder() {
    // Invoke the picker before awaiting anything else: browsers require a live user gesture.
    const selection = chooseDownloadDirectory()
    setChoosing(true)
    setMessage('')
    try {
      const selected = await selection
      if (!mounted.current) return
      setDirectory(selected)
      setMode('folder')
    } catch (error) {
      if (!mounted.current) return
      setMessage(error instanceof Error && error.name === 'AbortError'
        ? '已取消选择目录，保存设置未改变。'
        : '无法访问该目录，请重新选择，或改用 ZIP 下载。')
    } finally { if (mounted.current) setChoosing(false) }
  }

  async function start(selected: SearchImage[]) {
    if (active.current || !selected.length) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setMessage('')
    setProgress(null)
    setReport(null)
    setRunMode(mode)
    try {
      const result = await downloadImages({ items: selected, mode, directory, apiBase, signal: controller.signal,
        onProgress: value => { if (mounted.current) setProgress(value) } })
      if (mounted.current) setReport(result)
    } catch (error) {
      if (mounted.current) setMessage(`下载未完成：${error instanceof Error ? error.message : '请稍后重试'}`)
    } finally {
      active.current = null
      if (mounted.current) setBusy(false)
    }
  }

  const close = () => { active.current?.abort(); onClose() }
  const percent = progress?.total ? Math.round(progress.processed / progress.total * 100) : 0
  const successful = report && !report.cancelled && !report.error && report.saved > 0
  const retryItems = report?.failures.map(failure => failure.item) ?? []

  return <Dialog title="下载所选图片" onClose={close} wide footer={<>
    <span className="download-footer-note">已选 {items.length} 张图片</span>
    {busy ? <button className="btn btn-secondary" onClick={() => active.current?.abort()}><Square size={15} />停止下载</button>
      : <>
        <button className="btn btn-secondary" onClick={close}>关闭</button>
        {retryItems.length > 0 && <button className="btn btn-secondary" disabled={choosing || (mode === 'folder' && !directory)}
          onClick={() => void start(retryItems)}><RotateCcw size={15} />重试失败项（{retryItems.length}）</button>}
        <button className="btn btn-primary" disabled={!items.length || choosing || (mode === 'folder' && !directory)}
          onClick={() => void start(items)}><Download size={16} />{report ? '重新下载全部' : '开始下载'}</button>
      </>}
  </>}>
    <div className="download-dialog-content">
      <p className="download-intro">将所选搜索结果保存到设备。优先下载原图，无法获取时尝试大尺寸缩略图。</p>
      <fieldset className="download-modes" disabled={busy || choosing}>
        <legend>保存方式</legend>
        <label className={`download-mode ${mode === 'zip' ? 'is-selected' : ''}`}>
          <input type="radio" name="download-mode" checked={mode === 'zip'} onChange={() => setMode('zip')} />
          <Archive size={21} /><span><strong>打包为 ZIP</strong><small>一次下载全部图片及来源清单</small></span>
        </label>
        <label className={`download-mode ${mode === 'folder' ? 'is-selected' : ''} ${!folderSupported ? 'is-unavailable' : ''}`}>
          <input type="radio" name="download-mode" checked={mode === 'folder'} disabled={!folderSupported}
            onChange={() => setMode('folder')} />
          <FolderOpen size={21} /><span><strong>保存到指定目录</strong><small>{folderSupported ? '在所选位置创建独立文件夹，避免覆盖' : '当前浏览器暂不支持，请使用 ZIP'}</small></span>
        </label>
      </fieldset>

      {mode === 'folder' ? <div className="download-destination">
        <span><FolderOpen size={17} /><strong>{directory ? `已选择：${directory.name}` : '尚未选择保存目录'}</strong></span>
        <button className="btn btn-secondary" disabled={busy || choosing} onClick={() => void chooseFolder()}>
          {choosing ? '正在选择…' : directory ? '更换目录' : '选择目录'}</button>
        <small>浏览器仅提供目录名称；下载时会创建新的 images 文件夹，完整名称将在下方显示。</small>
      </div> : <p className="download-help">保存位置由浏览器的下载设置决定。如需每次指定路径，请在浏览器中开启“下载前询问保存位置”。</p>}

      <div className="download-limits">单次最多获取 200 MB 图片。来源网站可能限制跨域访问，失败项可重试或打开来源手动保存。</div>
      {message && <p className="download-status-note" role="status">{message}</p>}

      {progress && <section className="download-progress" aria-label="下载进度" aria-busy={busy}>
        <div className="download-progress-title"><strong>{busy
          ? progress.phase === 'packing' ? '正在生成 ZIP…' : '正在获取图片…'
          : report?.cancelled ? '下载已停止' : successful ? '本次下载已完成' : '本次下载未完成'}</strong>
          <span>{progress.processed} / {progress.total}</span></div>
        <progress max={progress.total || 1} value={progress.phase === 'packing' ? undefined : progress.processed}
          aria-label={progress.phase === 'packing' ? '正在打包' : `图片处理进度 ${percent}%`} />
        <div className="download-statistics"><span>已获取 <strong>{progress.prepared}</strong></span>
          <span>{runMode === 'folder' ? '已保存' : '已交给浏览器'} <strong>{progress.saved}</strong></span>
          <span>失败 <strong>{progress.failed}</strong></span><span>{(progress.bytes / 1024 / 1024).toFixed(1)} MB</span></div>
        {busy && progress.current && <p className="download-current"><LoaderCircle size={14} className="spin" /><span>{progress.current}</span></p>}
      </section>}

      {report && <div className={`download-report ${successful ? 'is-success' : ''}`} role="status">
        {successful ? <CheckCircle2 size={19} /> : <TriangleAlert size={19} />}
        <div>
          {report.cancelled ? <p>{runMode === 'folder'
            ? `已停止。此前已保存 ${report.saved} 张图片，文件仍保留在目录中。`
            : '已停止，本次未生成 ZIP，也未保存图片。'}</p>
            : report.error ? <p>下载未完成：{report.error}。{report.saved > 0 ? `已保存 ${report.saved} 张图片。` : '本次没有图片保存成功。'}</p>
              : report.saved > 0 ? <p>{runMode === 'folder'
                ? `已保存 ${report.saved} 张图片及 results.json 来源清单。`
                : `已将含 ${report.saved} 张图片及来源清单的 ZIP 交给浏览器，请查看下载列表确认保存结果。`}</p>
                : <p>本次没有图片下载成功，请查看失败原因后重试。</p>}
          {report.destination && <small className="download-report-destination">{report.destination}</small>}
          {report.cancelled && progress && progress.processed < progress.total && <small>还有 {progress.total - progress.processed} 张未处理，可点击“重新下载全部”重新开始。</small>}
        </div>
      </div>}

      {!!report?.failures.length && <section className="download-failures"><h3>失败记录 · {report.failures.length}</h3>
        <ul>{report.failures.map(({ item, reason }, index) => {
          const source = safeHttpUrl(item.imageUrl) ?? safeHttpUrl(item.thumbnailUrl) ?? safeHttpUrl(item.pageUrl)
          return <li key={`${item.id}-${index}`}><div><strong>{item.title}</strong><p>{reason}</p></div>
            {source && <a href={source} target="_blank" rel="noreferrer">打开来源<ExternalLink size={13} /></a>}</li>
        })}</ul>
      </section>}
    </div>
  </Dialog>
}
