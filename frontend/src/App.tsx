import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, Check, CheckCircle2,
  CircleHelp, Database, FolderOpen, ImageIcon, Images, LayoutGrid, List, Link2, PanelLeftClose, PanelLeftOpen,
  ListFilter, LoaderCircle, Menu, ScanLine, Search, Settings2, SlidersHorizontal,
  Tags, Upload, UploadCloud, Workflow, X, XCircle,
} from 'lucide-react'
import { ManagementViews, type ManagementView } from './components/ManagementViews'
import { Dialog } from './components/Dialog'
import { BatchSearchPanel, type BatchSearchResult } from './components/BatchSearchPanel'
import { DownloadDialog } from './components/DownloadDialog'
import { ImageFilePicker } from './components/ImageFilePicker'
import { downloadImage, getImageSources, safeHttpUrl, searchImages } from './lib/search'
import type { SearchImage, SearchResponse } from './types'

type View = 'search' | ManagementView
const navigation = [
  { key: 'search' as const, name: '图搜图', icon: ScanLine, description: '全网发现，相似检索' },
  { key: 'library' as const, name: '图片数据', icon: Images, description: '管理你的食材图库' },
  { key: 'import' as const, name: '上传与导入', icon: UploadCloud, description: '整理图片，准备入库' },
  { key: 'tasks' as const, name: '入库任务', icon: Workflow, description: '任务进度与失败记录' },
  { key: 'ingredients' as const, name: '食材管理', icon: Tags, description: '名称、类别与属性' },
]
const pageSize = 12
const maxBytes = 15 * 1024 * 1024

function loadBaseUrl() {
  try { return localStorage.getItem('food-atlas-api') ?? import.meta.env.VITE_API_BASE_URL ?? '' }
  catch { return import.meta.env.VITE_API_BASE_URL ?? '' }
}

function ResultImage({ item, className = '' }: { item: SearchImage; className?: string }) {
  const sources = getImageSources(item)
  const sourceKey = sources.join('\n')
  const [index, setIndex] = useState(0)
  useEffect(() => setIndex(0), [item.id, sourceKey])
  return sources[index]
    ? <img className={className} src={sources[index]} alt={item.title} loading="lazy" referrerPolicy="no-referrer" onError={() => setIndex(i => i + 1)} />
    : <div className={`image-unavailable ${className}`}><ImageIcon size={32} strokeWidth={1.3} /><span>图片暂不可用</span></div>
}

function QueryPreview({ src }: { src: string }) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  return <div className="query-preview-frame" aria-busy={status === 'loading'}>
    {status === 'error'
      ? <div className="query-preview query-preview-unavailable" role="status"><ImageIcon size={28} strokeWidth={1.4} /><span>图片预览暂不可用</span><small>可更换图片，或继续尝试搜索</small></div>
      : <img className="query-preview" src={src} alt="当前查询图片" referrerPolicy="no-referrer" decoding="async" onLoad={() => setStatus('ready')} onError={() => setStatus('error')} />}
    {status === 'loading' && <span className="query-preview-loading" role="status"><LoaderCircle size={16} className="spin" />正在加载预览…</span>}
  </div>
}

function Score({ value }: { value: number | null }) {
  return <span className={`score ${value === null ? 'score-unknown' : ''}`}>
    {value === null ? '未返回分数' : <><ScanLine size={12} />{(value * 100).toFixed(1)}% 相似</>}
  </span>
}

function ResultTable({ items, onDetail, onDownload, downloading, selected, onToggle }: {
  items: SearchImage[]; onDetail: (item: SearchImage) => void;
  onDownload: (item: SearchImage) => void; downloading: string | null;
  selected: Set<string>; onToggle: (id: string) => void
}) {
  return <div className="result-table-scroll"><table className="result-table">
    <thead><tr><th>选择</th><th>图片</th><th>图片名称</th><th>相似度</th><th>类别</th><th>来源</th><th>操作</th></tr></thead>
    <tbody>{items.map(item => <tr key={item.id} className={selected.has(item.id) ? 'result-row-selected' : ''}>
      <td><input type="checkbox" className="result-checkbox" aria-label={`选择图片：${item.title}`} checked={selected.has(item.id)} onChange={() => onToggle(item.id)} /></td>
      <td><button className="table-image" onClick={() => onDetail(item)} aria-label={`查看图片详情：${item.title}`}><ResultImage item={item} /></button></td>
      <th scope="row"><button className="table-title" onClick={() => onDetail(item)}>{item.title}</button></th>
      <td><Score value={item.similarity} /></td><td>{item.category ?? '—'}</td><td>{item.siteName ?? '—'}</td>
      <td><div className="table-actions"><button className="btn btn-secondary" onClick={() => onDetail(item)}>详情</button><button className="btn btn-secondary" disabled={Boolean(downloading)} aria-label={`下载图片：${item.title}`} onClick={() => onDownload(item)}>{downloading === item.id ? <LoaderCircle size={13} className="spin" /> : <ArrowDownToLine size={13} />}下载</button></div></td>
    </tr>)}</tbody>
  </table></div>
}

function EmptyResults() {
  return <div className="results-empty">
    <span className="empty-image-icon" aria-hidden="true"><Images size={34} strokeWidth={1.2} /></span>
    <h3>等待查询图片</h3><p>上传图片或粘贴图片链接后，点击「开始搜索」。</p>
    <div className="empty-steps"><span>上传图片</span><ArrowRight size={13} /><span>检索相似结果</span><ArrowRight size={13} /><span>查看与下载</span></div>
  </div>
}

export default function App() {
  const [view, setView] = useState<View>('search')
  const [mobileNav, setMobileNav] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [mode, setMode] = useState<'file' | 'url'>('file')
  const [queryKind, setQueryKind] = useState<'single' | 'batch'>('single')
  const [batchRunning, setBatchRunning] = useState(false)
  const [activeQueryName, setActiveQueryName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [url, setUrl] = useState('')
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [searched, setSearched] = useState(false)
  const [response, setResponse] = useState<SearchResponse | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('all')
  const [sort, setSort] = useState('default')
  const [resultLayout, setResultLayout] = useState<'grid' | 'list'>('grid')
  const [page, setPage] = useState(1)
  const [detail, setDetail] = useState<SearchImage | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [downloadFailure, setDownloadFailure] = useState<SearchImage | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloadItems, setDownloadItems] = useState<SearchImage[] | null>(null)
  const [apiBase, setApiBase] = useState(loadBaseUrl)
  const [apiDraft, setApiDraft] = useState(apiBase)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const activeRequest = useRef<AbortController | null>(null)

  const checkConnection = useCallback(async () => {
    setConnection('checking')
    try {
      const result = await fetch(`${apiBase.replace(/\/$/, '')}/api/ping`, { signal: AbortSignal.timeout(5000) })
      if (!result.ok) throw new Error('offline')
      const ping = await result.json()
      setConnection(ping.ok === true ? 'online' : 'offline')
    } catch { setConnection('offline') }
  }, [apiBase])
  useEffect(() => { void checkConnection() }, [checkConnection])
  useEffect(() => {
    if (!file) { setPreview(''); return }
    const objectUrl = URL.createObjectURL(file)
    setPreview(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  useEffect(() => () => activeRequest.current?.abort(), [])
  useEffect(() => { setPage(1) }, [keyword, category, sort])

  const resetResults = useCallback(() => {
    activeRequest.current?.abort()
    activeRequest.current = null
    setBusy(false); setError(''); setNotice(''); setResponse(null); setSearched(false)
    setPage(1); setCategory('all'); setKeyword(''); setElapsed(null); setDownloadFailure(null)
    setSelected(new Set()); setActiveQueryName(''); setDetail(null)
  }, [])
  const handleBatchRunningChange = useCallback((running: boolean) => {
    setBatchRunning(running)
    if (running) resetResults()
  }, [resetResults])
  const handleBatchResult = useCallback(({ response: result, file: queryFile, elapsed: seconds }: BatchSearchResult) => {
    setResponse(result); setSearched(true); setElapsed(seconds); setActiveQueryName(queryFile.name)
    setSelected(new Set()); setPage(1); setCategory('all'); setKeyword(''); setSort('default')
    setError(''); setNotice(result.message ?? ''); setDownloadFailure(null); setDetail(null)
  }, [])
  function toggleSelected(id: string) {
    setSelected(current => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  function selectFile(next: File | undefined) {
    if (!next) return
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/bmp'].includes(next.type)) {
      setError('请选择 JPG、PNG、WebP 或 BMP 格式的图片。'); return
    }
    if (next.size > maxBytes || next.size === 0) { setError('图片大小需大于 0 且不超过 15 MB。'); return }
    resetResults(); setFile(next); setMode('file')
  }
  async function runSearch() {
    if (busy || batchRunning) return
    if (mode === 'file' && !file) { setError('请先上传一张查询图片。'); return }
    if (mode === 'url' && !safeHttpUrl(url.trim())) { setError('请输入有效的 HTTP 或 HTTPS 图片链接。'); return }
    const controller = new AbortController()
    activeRequest.current?.abort(); activeRequest.current = controller
    setBusy(true); setError(''); setNotice(''); setResponse(null); setSearched(false)
    setCategory('all'); setKeyword(''); setPage(1); setDownloadFailure(null); setElapsed(null)
    setSelected(new Set()); setActiveQueryName(mode === 'file' ? file!.name : url.trim())
    const started = performance.now()
    const timer = window.setTimeout(() => controller.abort('timeout'), 120000)
    try {
      const result = await searchImages({ file: mode === 'file' ? file! : undefined, url: mode === 'url' ? url.trim() : undefined, baseUrl: apiBase, signal: controller.signal })
      if (activeRequest.current !== controller) return
      setResponse(result); setSearched(true); setElapsed((performance.now() - started) / 1000)
      if (result.message) setNotice(result.message)
    } catch (cause) {
      if (activeRequest.current !== controller) return
      setError(controller.signal.aborted ? '搜索等待超时，请稍后重试。' : cause instanceof Error ? cause.message : '搜索失败，请重试。')
    } finally {
      window.clearTimeout(timer)
      if (activeRequest.current === controller) { activeRequest.current = null; setBusy(false) }
    }
  }
  async function saveImage(item: SearchImage) {
    if (downloading) return
    setDownloading(item.id); setDownloadFailure(null); setError(''); setNotice('')
    try { await downloadImage(item, apiBase); setNotice('已发起图片下载，请在浏览器下载列表中查看。') }
    catch (cause) { setError(cause instanceof Error ? cause.message : '下载失败，请打开原图另存为。'); setDownloadFailure(item) }
    finally { setDownloading(null) }
  }
  function navigate(next: View) { setView(next); setMobileNav(false) }
  function saveSettings() {
    const next = apiDraft.trim().replace(/\/+$/, '')
    if (next && !safeHttpUrl(next)) { return }
    resetResults(); setApiBase(next)
    try { localStorage.setItem('food-atlas-api', next) } catch { /* settings remain usable for this session */ }
    setSettingsOpen(false)
  }

  const items = response?.items ?? []
  const categories = [...new Set(items.flatMap(item => item.category ? [item.category] : []))]
  const filtered = items.filter(item => (category === 'all' || item.category === category) && (!keyword || `${item.title} ${item.siteName ?? ''}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())))
  const sorted = sort === 'similarity' ? [...filtered].sort((a, b) => (b.similarity ?? -Infinity) - (a.similarity ?? -Infinity)) : filtered
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const currentPage = Math.min(page, pages)
  const visible = sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const pageInfo = navigation.find(item => item.key === view)!
  const inputReady = mode === 'file' ? Boolean(file) : Boolean(safeHttpUrl(url.trim()))
  const queryImageUrl = mode === 'url' ? safeHttpUrl(url.trim()) : null
  const waitingForResults = busy || (queryKind === 'batch' && batchRunning && !searched)
  const selectedItems = items.filter(item => selected.has(item.id))
  const allPageSelected = visible.length > 0 && visible.every(item => selected.has(item.id))
  const somePageSelected = visible.some(item => selected.has(item.id))
  function togglePageSelection() {
    setSelected(current => {
      const next = new Set(current)
      const remove = visible.every(item => next.has(item.id))
      visible.forEach(item => { if (remove) next.delete(item.id); else next.add(item.id) })
      return next
    })
  }

  return <div className={`app-shell ${navCollapsed ? 'nav-collapsed' : ''}`}>
    {mobileNav && <button className="sidebar-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
      <div className="sidebar-heading"><span>导航</span><button className="icon-button collapse-navigation" aria-label={navCollapsed ? '展开侧栏' : '收起侧栏'} title={navCollapsed ? '展开侧栏' : '收起侧栏'} aria-expanded={!navCollapsed} onClick={() => setNavCollapsed(value => !value)}>{navCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></div>
      <nav aria-label="主导航"><span className="nav-group-label">图像检索</span>
        {navigation.map(({ key, icon: Icon, name }, index) => <div key={key}>
          {index === 1 && <span className="nav-group-label nav-group-second">数据管理</span>}
          <button className={`nav-item ${view === key ? 'active' : ''}`} onClick={() => navigate(key)} aria-label={name} title={name} aria-current={view === key ? 'page' : undefined}>
            <span className="nav-icon"><Icon size={16} strokeWidth={1.6} /></span><span className="nav-label">{name}</span>{key !== 'search' && <small>待接入</small>}
          </button>
        </div>)}
      </nav>
      <div className="sidebar-bottom">
        <button className="sidebar-connection" disabled={batchRunning} onClick={() => { setApiDraft(apiBase); setSettingsOpen(true) }}><span className={`status-dot ${connection}`} /><span>{connection === 'online' ? '搜索服务可连接' : connection === 'checking' ? '检查服务连接…' : '搜索服务未连接'}</span><Settings2 size={15} /></button>
        <div className="sidebar-footer"><span className="avatar"><Database size={17} /></span><div>图像管理工作台<small>前端预览 · v0.1</small></div></div>
      </div>
    </aside>

    <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="展开导航" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="topbar-title"><h1>{pageInfo.name}</h1><span>食材图像管理 / {pageInfo.description}</span></div></div><div className="topbar-actions"><button className="icon-button" title="使用说明" aria-label="使用说明" onClick={() => setHelpOpen(true)}><CircleHelp size={17} /></button><button className="icon-button" title="连接设置" aria-label="连接设置" disabled={batchRunning} onClick={() => { setApiDraft(apiBase); setSettingsOpen(true) }}><Settings2 size={17} /></button></div></header>
    <main className={view === 'search' ? 'search-page' : 'management-page'}>
      {view !== 'search' && <ManagementViews view={view} onNavigate={navigate} />}
      <div className="search-view" hidden={view !== 'search'}>
        {(error || notice) && <div className={`message-banner ${error ? 'message-error' : 'message-success'}`} role={error ? 'alert' : 'status'}>{error ? <XCircle size={18} /> : <CheckCircle2 size={18} />}<span>{error || notice}</span>{downloadFailure && safeHttpUrl(downloadFailure.imageUrl ?? downloadFailure.thumbnailUrl ?? '') && <a href={downloadFailure.imageUrl ?? downloadFailure.thumbnailUrl!} target="_blank" rel="noreferrer">打开原图 <ArrowUpRight size={14} /></a>}<button className="icon-button" aria-label="关闭提示" onClick={() => { setError(''); setNotice(''); setDownloadFailure(null) }}><X size={16} /></button></div>}
        <div className="search-workspace">
          <section className="query-panel panel" aria-label="图像搜索"><div className="query-toolbar">
            <div className="query-kind-tabs" aria-label="搜索模式">
              <button aria-pressed={queryKind === 'single'} className={queryKind === 'single' ? 'active' : ''} disabled={batchRunning} onClick={() => { if (queryKind !== 'single') { resetResults(); setQueryKind('single') } }}>单张搜索</button>
              <button aria-pressed={queryKind === 'batch'} className={queryKind === 'batch' ? 'active' : ''} disabled={busy} onClick={() => { if (queryKind !== 'batch') { resetResults(); setQueryKind('batch') } }}>批量搜索</button>
            </div>
            <span className="subtle-label">{queryKind === 'batch' ? '≤30 张 · 15 MB/张' : '≤15 MB'}</span>
            </div>
            <div hidden={queryKind !== 'single'}>
            <div className="query-body"><div className="query-source"><div className="segmented" aria-label="查询方式"><button className={mode === 'file' ? 'selected' : ''} aria-pressed={mode === 'file'} onClick={() => { if (mode !== 'file') { resetResults(); setMode('file') } }}><Upload size={14} />本地上传</button><button className={mode === 'url' ? 'selected' : ''} aria-pressed={mode === 'url'} onClick={() => { if (mode !== 'url') { resetResults(); setMode('url') } }}><Link2 size={14} />图片链接</button></div>
              {mode === 'file' ? <div className={`upload-area ${dragging ? 'is-dragging' : ''} ${file ? 'has-image' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); selectFile(event.dataTransfer.files[0]) }}>
                {file && preview ? <><QueryPreview key={preview} src={preview} /><div className="selected-file-info"><b>{file.name}</b><span>{(file.size / 1024).toFixed(0)} KB · 已选择查询图片</span><ImageFilePicker className="replace-image" label="选择查询图片" onFiles={files => selectFile(files[0])}><Upload size={13} />更换图片</ImageFilePicker></div><button className="remove-image" aria-label="移除查询图片" onClick={() => { resetResults(); setFile(null) }}><X size={15} /></button></> : <ImageFilePicker className="upload-trigger" label="选择查询图片" onFiles={files => selectFile(files[0])}><span className="upload-icon"><UploadCloud size={25} strokeWidth={1.3} /></span><span className="upload-copy"><b>点击上传，或拖拽图片至此</b><small>支持 JPG、PNG、WebP、BMP 格式</small></span></ImageFilePicker>}
              </div> : <div className="url-input-area"><label className="sr-only" htmlFor="image-url">图片地址</label><input id="image-url" className="input" type="url" placeholder="https://example.com/image.jpg" value={url} onChange={event => { resetResults(); setUrl(event.target.value) }} /><p>请填写可公开访问的图片直链。</p>{queryImageUrl && <QueryPreview key={queryImageUrl} src={queryImageUrl} />}</div>}
            </div><div className="query-actions">
              <button className="btn btn-primary search-button" onClick={() => void runSearch()} disabled={!inputReady || busy}>{busy ? <LoaderCircle size={15} className="spin" /> : <Search size={15} />}{busy ? '正在搜索相似图片…' : '开始搜索'}</button>
              {busy && <button className="text-button cancel-search" onClick={() => { activeRequest.current?.abort(); activeRequest.current = null; setBusy(false); setNotice('已取消本次搜索。') }}>取消搜索</button>}
              <button className="btn btn-secondary" disabled={!file && !url && !searched && !error} onClick={() => { resetResults(); setFile(null); setUrl('') }}>重置</button>
            </div>
            </div>
            </div>
            <div hidden={queryKind !== 'batch'}><BatchSearchPanel active={queryKind === 'batch'} baseUrl={apiBase} onResult={handleBatchResult} onClearResult={resetResults} onRunningChange={handleBatchRunningChange} /></div>
          </section>

          <section className="results-panel panel" aria-labelledby="results-heading"><div className="panel-heading results-heading"><div><h2 id="results-heading">搜索结果</h2>{searched && <span className="count-badge">{items.length}</span>}</div><span className="subtle-label">{elapsed !== null ? `检索耗时 ${elapsed.toFixed(2)} s` : 'Global 图片搜索'}</span></div>
            <div className="result-toolbar"><div className="filter-input"><Search size={15} /><input aria-label="搜索结果标题或来源" placeholder="搜索结果标题或来源" value={keyword} disabled={!items.length} onChange={event => setKeyword(event.target.value)} /></div><div className="toolbar-select"><ListFilter size={15} /><select aria-label="筛选类别" value={category} disabled={!categories.length} onChange={event => setCategory(event.target.value)}><option value="all">全部类别</option>{categories.map(name => <option key={name} value={name}>{name}</option>)}</select></div><div className="toolbar-select"><SlidersHorizontal size={15} /><select aria-label="结果排序" value={sort} disabled={!items.length} onChange={event => setSort(event.target.value)}><option value="default">默认排序</option><option value="similarity">相似度优先</option></select></div><div className="view-toggle" aria-label="搜索结果展示方式"><button className={resultLayout === 'grid' ? 'selected' : ''} aria-label="网格展示结果" aria-pressed={resultLayout === 'grid'} onClick={() => setResultLayout('grid')}><LayoutGrid size={16} /></button><button className={resultLayout === 'list' ? 'selected' : ''} aria-label="列表展示结果" aria-pressed={resultLayout === 'list'} onClick={() => setResultLayout('list')}><List size={17} /></button></div></div>
            {items.length > 0 && <div className="selection-bar">
              <label className="select-page"><input className="result-checkbox" type="checkbox" aria-label="选择本页" checked={allPageSelected} ref={element => { if (element) element.indeterminate = somePageSelected && !allPageSelected }} onChange={togglePageSelection} />选择本页</label>
              <button className="text-button" onClick={() => setSelected(new Set(items.map(item => item.id)))}>选择全部结果</button>
              <span className="selection-count" role="status">已选择 {selectedItems.length} 张</span>
              {selectedItems.length > 0 && <button className="text-button" onClick={() => setSelected(new Set())}>清空选择</button>}
              <button className="btn btn-primary bulk-download-button" disabled={!selectedItems.length} onClick={() => setDownloadItems(selectedItems)}><ArrowDownToLine size={15} />批量下载{selectedItems.length > 0 ? `（${selectedItems.length}）` : ''}</button>
            </div>}
            {activeQueryName && searched && <div className="active-query-name" title={activeQueryName}><ImageIcon size={14} /><span>当前查询：{activeQueryName}</span></div>}
            <div className={`results-content ${resultLayout === 'list' ? 'content-list' : ''}`}>
            {waitingForResults ? <div className="loading-state" role="status"><div className="loading-orbit"><ScanLine size={37} strokeWidth={1.3} /></div><h3>正在寻找图片之间的联系</h3><p>{queryKind === 'batch' ? '批量搜索正在进行，查询图片完成后会显示对应结果。' : '搜索、获取图片并计算相似度，请稍候…'}</p><div className="skeleton-row">{[1, 2, 3].map(n => <div className="skeleton-card" key={n} />)}</div></div>
              : !searched ? <EmptyResults />
              : !visible.length ? <div className="no-results"><Search size={35} strokeWidth={1.2} /><h3>{items.length ? '没有符合筛选条件的结果' : '暂未找到相关图片'}</h3><p>{items.length ? '试试其他关键词或类别。' : '试试更清晰的图片，或更换查询主体。'}</p>{items.length > 0 && <button className="btn btn-secondary" onClick={() => { setKeyword(''); setCategory('all') }}>清除筛选</button>}</div>
              : resultLayout === 'list' ? <ResultTable items={visible} onDetail={setDetail} onDownload={item => void saveImage(item)} downloading={downloading} selected={selected} onToggle={toggleSelected} />
              : <div className="result-grid">{visible.map(item => <article className={`result-card ${selected.has(item.id) ? 'result-selected' : ''}`} key={item.id}><label className="image-select"><input className="result-checkbox" type="checkbox" aria-label={`选择图片：${item.title}`} checked={selected.has(item.id)} onChange={() => toggleSelected(item.id)} /></label><button className="result-image-button" onClick={() => setDetail(item)} aria-label={`查看图片详情：${item.title}`}><ResultImage item={item} /><Score value={item.similarity} /><span className="image-hover-hint"><Search size={15} />查看详情</span></button><div className="result-card-body"><h3><button onClick={() => setDetail(item)}>{item.title}</button></h3><div className="result-card-meta"><span>{item.category ?? item.siteName ?? 'Global 搜索'}</span><button className="icon-button download-button" title="下载图片" aria-label={`下载图片：${item.title}`} disabled={Boolean(downloading)} onClick={() => void saveImage(item)}>{downloading === item.id ? <LoaderCircle size={16} className="spin" /> : <ArrowDownToLine size={16} />}</button></div></div></article>)}</div>}
            </div>
            <div className="results-footer"><span>{searched ? `共 ${items.length} 张图片${filtered.length !== items.length ? ` · 筛选后 ${filtered.length} 张` : ''}` : '搜索结果将在这里展示'}{items.length > 0 && !categories.length && <small> · 暂无类别数据</small>}</span><div className="pagination"><button className="icon-button" aria-label="上一页" disabled={currentPage <= 1 || !items.length} onClick={() => setPage(currentPage - 1)}><ArrowLeft size={15} /></button><span>{currentPage} <small>/ {pages}</small></span><button className="icon-button" aria-label="下一页" disabled={currentPage >= pages || !items.length} onClick={() => setPage(currentPage + 1)}><ArrowRight size={15} /></button></div></div>
          </section>
        </div>
        <div className="search-bottom-note">{response?.traceId && <span className="trace-id" title={response.traceId}>请求 ID：{response.traceId}</span>}<span className="local-filter-note">类别筛选与分页仅作用于本次搜索结果</span></div>
      </div>
    </main></div>

    {detail && <Dialog title="图片详情" onClose={() => setDetail(null)} wide footer={<footer className="detail-footer"><p>图片来自外部来源，请根据来源授权使用。</p><div className="detail-actions"><button className="btn btn-secondary" onClick={() => setDownloadItems([detail])}><FolderOpen size={14} />保存选项</button><button className="btn btn-secondary" onClick={() => setDetail(null)}>关闭</button>{detail.pageUrl && <a className="btn btn-secondary" href={detail.pageUrl} target="_blank" rel="noreferrer">来源网页 <ArrowUpRight size={13} /></a>}<button className="btn btn-primary" disabled={Boolean(downloading)} onClick={() => void saveImage(detail)}>{downloading === detail.id ? <LoaderCircle size={14} className="spin" /> : <ArrowDownToLine size={14} />}下载图片</button></div></footer>}>
      <div className="detail-content">
        <section className="detail-section"><h3>检索信息</h3>
          <div className="detail-summary"><h4>{detail.title}</h4><Score value={detail.similarity} /></div>
          <dl className="detail-fields"><div><dt>食材类别</dt><dd>{detail.category ?? '未提供类别'}</dd></div><div><dt>图片尺寸</dt><dd>{detail.width && detail.height ? `${detail.width} × ${detail.height} px` : '未提供'}</dd></div><div><dt>图片来源</dt><dd>{detail.siteName ?? '未提供'}</dd></div><div><dt>结果编号</dt><dd className="monospace">{detail.id}</dd></div></dl>
        </section>
        <section className="detail-section"><h3>图片预览</h3><div className="detail-image"><ResultImage item={detail} /></div></section>
        <section className="detail-section"><h3>来源信息</h3><dl className="detail-links"><div><dt>来源网页</dt><dd>{detail.pageUrl ? <a href={detail.pageUrl} target="_blank" rel="noreferrer">{detail.pageUrl}<ArrowUpRight size={12} /></a> : '—'}</dd></div><div><dt>图片地址</dt><dd>{(detail.imageUrl || detail.thumbnailUrl) ? <a className="original-link" href={detail.imageUrl ?? detail.thumbnailUrl!} target="_blank" rel="noreferrer">打开原图 / 另存为 <ArrowUpRight size={12} /></a> : '—'}</dd></div></dl>
          {detail.snippets.length > 0 && <div className="detail-description"><span>来源描述</span><div>{detail.snippets.map((text, i) => <p key={i}>{text}</p>)}</div></div>}
        </section>
        {downloadFailure?.id === detail.id && <p className="inline-error" role="alert">{error}</p>}
      </div>

    </Dialog>}
    {downloadItems && <DownloadDialog items={downloadItems} apiBase={apiBase} onClose={() => setDownloadItems(null)} />}
    {settingsOpen && <Dialog title="搜索服务连接" onClose={() => setSettingsOpen(false)}><div className="settings-content"><p>连接现有的图像搜索服务。留空时使用当前站点的接口。</p><label htmlFor="api-base">服务地址</label><input className="input" id="api-base" type="url" placeholder="默认使用站点 /api" value={apiDraft} onChange={event => setApiDraft(event.target.value)} /><small>例如 http://127.0.0.1:8320，不包含 /api 路径。跨域连接需服务端允许访问。</small>{apiDraft.trim() && !safeHttpUrl(apiDraft.trim()) && <p className="inline-error">请输入 HTTP 或 HTTPS 地址。</p>}<div className="connection-info"><span className={`status-dot ${connection}`} />{connection === 'online' ? '接口可连接' : connection === 'checking' ? '正在检查连接…' : '当前未连接，请检查服务地址与后端运行状态'}<button className="text-button" onClick={() => void checkConnection()}>重新检查</button></div><div className="dialog-actions"><button className="btn btn-secondary" onClick={() => setSettingsOpen(false)}>取消</button><button className="btn btn-primary" disabled={Boolean(apiDraft.trim() && !safeHttpUrl(apiDraft.trim()))} onClick={saveSettings}><Check size={16} />保存连接</button></div></div></Dialog>}
    {helpOpen && <Dialog title="开始使用食图" onClose={() => setHelpOpen(false)}><div className="help-content"><div><Search size={21} /><section><h3>以图搜图</h3><p>上传图片或输入可公开访问的图片直链，点击搜索。切换到「批量搜索」可添加最多 30 张图片，逐张搜索并在队列中分别查看结果；支持停止、继续和重试。</p></section></div><div><ArrowDownToLine size={21} /><section><h3>查看与下载</h3><p>点击结果打开详情，勾选图片后可「批量下载」。支持跨页多选，保存为 ZIP 或选择本地目录，并附带来源与相似度清单。目录选择需浏览器支持，并通过 HTTPS 或 localhost 访问；也可使用 ZIP 下载。</p></section></div><div><FolderOpen size={21} /><section><h3>为数据管理做好准备</h3><p>图片库、批量入库和任务接口待接入。导入队列与食材编辑仅为本地草稿，不会写入后台。</p></section></div><div className="help-note">相似度仅在服务返回分数时展示；类别不从图片标题自动推断。</div></div></Dialog>}
  </div>
}
