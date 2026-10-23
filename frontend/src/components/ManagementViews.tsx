import { useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowRight, Check, ChevronLeft, ChevronRight, CloudUpload, FolderOpen, ImagePlus, Images, Info, Layers3, Leaf, ListChecks, Pencil, Plus, Search, Tags, Trash2, X } from 'lucide-react'
import { Dialog } from './Dialog'
import './ManagementViews.css'

export type ManagementView = 'library' | 'import' | 'tasks' | 'ingredients'
type Props = { view: ManagementView; onNavigate: (view: ManagementView) => void }
type UploadImage = { id: string; file: File; preview: string }
type Ingredient = { id: string; name: string; category: string; attributes: string }
const categories = ['蔬菜', '水果', '肉禽', '水产', '蛋奶', '粮油', '调味品', '其他']
const MAX_IMAGE_SIZE = 15 * 1024 * 1024
const MAX_QUEUE = 100
const taskStatuses = ['全部任务', '处理中', '已完成', '失败记录']
let nextDraftId = 0
// Draft IDs only need to be unique within this browser session. This also works
// when the dev server is opened over plain HTTP from another device.
function draftId() { return `draft-${++nextDraftId}` }
const titles: Record<ManagementView, { title: string; description: string }> = {
  library: { title: '图片数据管理', description: '管理食材图片、类别与入库信息' },
  import: { title: '上传与批量导入', description: '选择本地图片，统一整理入库信息' },
  tasks: { title: '入库任务', description: '查看任务进度与失败记录' },
  ingredients: { title: '食材管理', description: '维护食材名称、类别与属性' },
}

function EmptyState({ icon: Icon, title, description, children }: {
  icon: typeof Images; title: string; description: string; children?: ReactNode
}) {
  return <div className="mg-empty"><span className="mg-empty-icon"><Icon size={29} strokeWidth={1.4} /></span>
    <h3>{title}</h3><p>{description}</p>{children}</div>
}

export function ManagementViews({ view, onNavigate }: Props) {
  const [libraryQuery, setLibraryQuery] = useState('')
  const [libraryCategory, setLibraryCategory] = useState('全部类别')
  const [libraryLayout, setLibraryLayout] = useState<'grid' | 'list'>('grid')
  const [taskStatus, setTaskStatus] = useState('全部任务')
  const [queue, setQueue] = useState<UploadImage[]>([])
  const queueRef = useRef(queue)
  queueRef.current = queue
  const [fileIssues, setFileIssues] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [importCategory, setImportCategory] = useState('未分类')
  const [preview, setPreview] = useState<UploadImage | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [ingredients, setIngredients] = useState<Ingredient[]>([])
  const [ingredientQuery, setIngredientQuery] = useState('')
  const [ingredientCategory, setIngredientCategory] = useState('全部类别')
  const [editor, setEditor] = useState<Ingredient | null>(null)
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => () => { queueRef.current.forEach(item => URL.revokeObjectURL(item.preview)) }, [])

  function addFiles(files: FileList | File[]) {
    const issues: string[] = []
    const additions: UploadImage[] = []
    const existing = new Set(queue.map(item => `${item.file.name}:${item.file.size}:${item.file.lastModified}`))
    for (const file of Array.from(files)) {
      const key = `${file.name}:${file.size}:${file.lastModified}`
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        issues.push(`${file.name}：仅支持 JPG、PNG、WebP 图片`)
      } else if (file.size === 0 || file.size > MAX_IMAGE_SIZE) {
        issues.push(`${file.name}：图片不能为空，且不能超过 15 MB`)
      } else if (existing.has(key)) {
        issues.push(`${file.name}：已在待导入列表中`)
      } else if (queue.length + additions.length >= MAX_QUEUE) {
        issues.push(`${file.name}：单次最多准备 100 张图片`)
      } else {
        existing.add(key)
        additions.push({ id: draftId(), file, preview: URL.createObjectURL(file) })
      }
    }
    setQueue(current => [...current, ...additions])
    setFileIssues(issues)
  }

  function removeImage(id: string) {
    const item = queue.find(image => image.id === id)
    if (item) URL.revokeObjectURL(item.preview)
    setQueue(current => current.filter(image => image.id !== id))
    if (preview?.id === id) setPreview(null)
  }

  function clearQueue() {
    queue.forEach(item => URL.revokeObjectURL(item.preview))
    setQueue([])
    setPreview(null)
    setFileIssues([])
  }

  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
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
  const metadata = titles[view]
  return <div className="mg-page">
    <header className="mg-header"><div><h1>{metadata.title}</h1><p>{metadata.description}</p></div>
      <span className="mg-status"><span />{view === 'import' || view === 'ingredients' ? '本地草稿模式' : '等待数据接口'}</span>
    </header>

    {view === 'library' && <>
      <div className="mg-metrics"><Metric label="图片总量" value="—" detail="接入图库后显示" icon={Images} /><Metric label="食材类别" value="—" detail="接入分类后显示" icon={Layers3} /><Metric label="待入库任务" value="—" detail="接入任务后显示" icon={ListChecks} /></div>
      <section className="mg-panel"><div className="mg-section-top"><div><h2>图片资源</h2><p>管理所有已入库的食材图片</p></div><button className="mg-btn mg-primary" onClick={() => onNavigate('import')}><Plus size={17} />导入图片</button></div>
        <div className="mg-toolbar"><label className="mg-search"><Search size={17} /><input aria-label="搜索图库" placeholder="搜索图片名称或食材…" value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} />{libraryQuery && <button aria-label="清空搜索" onClick={() => setLibraryQuery('')}><X size={15} /></button>}</label>
          <select className="mg-select" aria-label="图库类别筛选" value={libraryCategory} onChange={event => setLibraryCategory(event.target.value)}><option>全部类别</option>{categories.map(category => <option key={category}>{category}</option>)}</select>
          <div className="mg-view-toggle" aria-label="图片展示方式"><button className={libraryLayout === 'grid' ? 'active' : ''} aria-label="网格展示" aria-pressed={libraryLayout === 'grid'} onClick={() => setLibraryLayout('grid')}><Images size={17} /></button><button className={libraryLayout === 'list' ? 'active' : ''} aria-label="列表展示" aria-pressed={libraryLayout === 'list'} onClick={() => setLibraryLayout('list')}><ListChecks size={17} /></button></div>
        </div>
        {libraryLayout === 'list' && <div className="mg-table-head mg-library-head"><span>图片 / 名称</span><span>类别</span><span>属性</span><span>入库时间</span></div>}
        <EmptyState icon={Images} title="暂无已入库图片" description={libraryQuery || libraryCategory !== '全部类别' ? '图库尚未开放，暂无可用于搜索和筛选的图片。' : '可先整理本地图片，图库开放后提交入库。'}>
          <button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}><ImagePlus size={17} />准备第一批图片<ArrowRight size={16} /></button>
        </EmptyState>
        <div className="mg-pagination"><span>暂无已入库图片</span><div><span>每页 24 张</span><button aria-label="上一页" disabled><ChevronLeft size={16} /></button><span>—</span><button aria-label="下一页" disabled><ChevronRight size={16} /></button></div></div>
      </section>
      <div className="mg-info"><Info size={17} /><p>后续接入图片详情、类别筛选与分页；当前不会创建或修改后端数据。</p></div>
    </>}

    {view === 'import' && <div className="mg-import-layout"><section className="mg-panel mg-import-main"><div className="mg-section-top"><div><h2>选择本地图片</h2><p>单张上传或批量选择，一次最多 100 张</p></div><span className="mg-step">01 / 准备图片</span></div>
      <div className={`mg-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}>
        <span className="mg-drop-icon"><CloudUpload size={32} strokeWidth={1.5} /></span><h3>把图片拖到这里</h3><p>或从你的设备中选择图片</p><button className="mg-btn mg-primary" onClick={() => fileInput.current?.click()}><FolderOpen size={17} />选择图片</button><small>JPG、PNG、WebP · 每张最大 15 MB</small>
        <input ref={fileInput} type="file" className="mg-hidden" aria-label="选择待导入图片" accept="image/jpeg,image/png,image/webp" multiple onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />
      </div>
      {fileIssues.length > 0 && <div className="mg-errors" role="alert"><div><strong>部分图片未添加</strong><button onClick={() => setFileIssues([])} aria-label="关闭文件提示"><X size={16} /></button></div><ul>{fileIssues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div>}
      <div className="mg-queue-heading"><h3>待导入图片 <span>{queue.length}</span></h3>{queue.length > 0 && <button className="mg-text-button" onClick={clearQueue}>清空列表</button>}</div>
      {queue.length === 0 ? <div className="mg-queue-empty"><Images size={22} /><p>选好的图片会在这里显示</p></div> : <div className="mg-upload-grid">{queue.map(item => <article className="mg-upload-card" key={item.id}>
        <button className="mg-upload-preview" onClick={() => setPreview(item)} aria-label={`预览 ${item.file.name}`}><img src={item.preview} alt={item.file.name} /><span>查看图片</span></button><button className="mg-remove-image" aria-label={`移除 ${item.file.name}`} onClick={() => removeImage(item.id)}><X size={14} /></button><div><strong title={item.file.name}>{item.file.name}</strong><small>{(item.file.size / 1024 / 1024).toFixed(2)} MB <span>待提交</span></small></div></article>)}</div>}
    </section><aside className="mg-import-aside"><section className="mg-panel mg-import-settings"><h2>整理入库信息</h2><p>为本次图片选择统一类别。</p><label className="mg-field">食材类别<select className="mg-select" value={importCategory} onChange={event => setImportCategory(event.target.value)}><option>未分类</option>{categories.map(category => <option key={category}>{category}</option>)}</select></label><div className="mg-import-summary"><span>已选择图片<strong>{queue.length} 张</strong></span><span>文件总大小<strong>{(queue.reduce((total, item) => total + item.file.size, 0) / 1024 / 1024).toFixed(2)} MB</strong></span><span>默认类别<strong>{importCategory}</strong></span></div><button className="mg-btn mg-primary mg-full" disabled aria-describedby="import-pending"><CloudUpload size={17} />提交入库 · 待接入</button><p id="import-pending" className="mg-help">入库接口尚未开发，当前仅在浏览器内预览与整理，不会上传文件。离开页面后草稿不保留。</p></section>
      <div className="mg-workflow-note"><ListChecks size={18} /><h3>入库任务</h3><p>未来提交后，可以在任务中心查看处理进度与失败原因。</p><button className="mg-text-button" onClick={() => onNavigate('tasks')}>了解任务中心<ArrowRight size={15} /></button></div></aside></div>}

    {view === 'tasks' && <>
      <div className="mg-metrics"><Metric label="正在处理" value="—" detail="等待任务服务接入" icon={CloudUpload} /><Metric label="成功入库" value="—" detail="等待任务服务接入" icon={Check} /><Metric label="失败记录" value="—" detail="等待任务服务接入" icon={ListChecks} /></div>
      <section className="mg-panel"><div className="mg-section-top"><div><h2>入库记录</h2><p>任务进度与失败记录统一查看</p></div><button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}><Plus size={16} />准备图片</button></div><div className="mg-tabs" role="tablist" aria-label="任务状态">{taskStatuses.map((status, index) => <button key={status} id={`task-tab-${index}`} role="tab" aria-selected={taskStatus === status} aria-controls="task-panel" tabIndex={taskStatus === status ? 0 : -1} className={taskStatus === status ? 'active' : ''} onClick={() => setTaskStatus(status)} onKeyDown={event => navigateTaskTabs(event, index)}>{status}</button>)}</div><div id="task-panel" role="tabpanel" aria-labelledby={`task-tab-${taskStatuses.indexOf(taskStatus)}`}><div className="mg-table-head mg-task-head"><span>任务名称</span><span>处理进度</span><span>状态</span><span>创建时间</span></div><EmptyState icon={ListChecks} title={taskStatus === '失败记录' ? '暂无可查看的失败记录' : taskStatus === '处理中' ? '暂无正在处理的任务' : taskStatus === '已完成' ? '暂无已完成的任务' : '暂无入库任务'} description="任务接口尚未接入。正式开放后，这里会显示真实进度、处理数量与失败原因。"><button className="mg-btn mg-secondary" onClick={() => onNavigate('import')}>先准备一批图片<ArrowRight size={16} /></button></EmptyState></div></section>
      <div className="mg-info"><Info size={17} /><p>后续支持查看失败详情与重试；页面当前不会生成模拟任务或模拟进度。</p></div>
    </>}

    {view === 'ingredients' && <>
      <section className="mg-panel"><div className="mg-section-top"><div><h2>食材字典 <span className="mg-count">{ingredients.length}</span></h2><p>维护名称、类别与属性，当前内容仅为本次会话的本地草稿</p></div><button className="mg-btn mg-primary" onClick={() => openEditor()}><Plus size={17} />新建食材</button></div>
        <div className="mg-toolbar"><label className="mg-search"><Search size={17} /><input aria-label="搜索食材草稿" placeholder="搜索食材名称或属性…" value={ingredientQuery} onChange={event => setIngredientQuery(event.target.value)} />{ingredientQuery && <button aria-label="清空食材搜索" onClick={() => setIngredientQuery('')}><X size={15} /></button>}</label><select className="mg-select" aria-label="食材类别筛选" value={ingredientCategory} onChange={event => setIngredientCategory(event.target.value)}><option>全部类别</option>{categories.map(category => <option key={category}>{category}</option>)}</select><span className="mg-draft-label">本地草稿 · 未入库</span></div>
        <div className="mg-ingredient-table"><div className="mg-table-head mg-ingredient-row"><span>食材名称</span><span>类别</span><span>食材属性</span><span>操作</span></div>{filteredIngredients.length ? filteredIngredients.map(item => <div className="mg-ingredient-row mg-table-row" key={item.id}><strong><span className="mg-leaf-icon"><Leaf size={17} /></span>{item.name}</strong><span><span className="mg-category-chip">{item.category}</span></span><span className="mg-attributes">{item.attributes || '未填写'}</span><div className="mg-row-actions"><button aria-label={`编辑 ${item.name}`} onClick={() => openEditor(item)}><Pencil size={16} /></button><button aria-label={`删除 ${item.name}`} onClick={() => { setIngredients(current => current.filter(ingredient => ingredient.id !== item.id)); setNotice(`已删除「${item.name}」的本地草稿`) }}><Trash2 size={16} /></button></div></div>) : <EmptyState icon={Tags} title={ingredients.length ? '没有匹配的食材草稿' : '暂无食材草稿'} description={ingredients.length ? '试试其他食材名称、属性或类别。' : '从名称、类别和属性开始，先在本地整理你的食材数据。'}>{ingredients.length ? <button className="mg-btn mg-secondary" onClick={() => { setIngredientQuery(''); setIngredientCategory('全部类别') }}>重置筛选</button> : <button className="mg-btn mg-secondary" onClick={() => openEditor()}><Plus size={16} />新建食材草稿</button>}</EmptyState>}</div>
      </section><div className="mg-info"><Info size={17} /><p>食材数据接口尚未接入。本地草稿不会写入数据库，刷新页面或离开后将清空。</p></div>
    </>}

    <div className="mg-live-status" role="status" aria-live="polite">{notice}</div>
    {preview && <Dialog title="图片预览" onClose={() => setPreview(null)} wide><img className="mg-dialog-image" src={preview.preview} alt={preview.file.name} /><p className="mg-preview-name">{preview.file.name}</p></Dialog>}
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

function Metric({ label, value, detail, icon: Icon }: { label: string; value: string; detail: string; icon: typeof Images }) {
  return <div className="mg-metric"><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div><span className="mg-metric-icon"><Icon size={22} strokeWidth={1.5} /></span></div>
}
