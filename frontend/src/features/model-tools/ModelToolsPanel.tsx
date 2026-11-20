import { useEffect, useId, useRef, useState } from 'react'
import { Crosshair, ImageIcon, LoaderCircle, Play, Scissors, Upload, UploadCloud, X, type LucideIcon } from 'lucide-react'
import { ImageFilePicker } from '../../components/ImageFilePicker'
import { EmptyState, notify } from '../../shared/ui'
import { detectObjects, segmentImage } from './api'
import { DetectionView } from './DetectionView'
import { SegmentationView } from './SegmentationView'
import { captionFrom, isToolKey, thresholdFrom, type DetectionResult, type SegmentResult, type ToolKey } from './model'
import './model-tools.css'

const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp']
const MAX_BYTES = 15 * 1024 * 1024
/** Same as 图搜图: a loaded GPU can take far longer than the usual second or two. */
const TIMEOUT_MS = 120_000

const TOOLS: ReadonlyArray<{ key: ToolKey; label: string; icon: LucideIcon; title: string; run: string; running: string }> = [
  { key: 'gdino', label: '目标检测', icon: Crosshair, title: 'GroundingDINO：按文字在图里找目标（POST /api/tools/gdino）', run: '开始检测', running: '正在检测…' },
  { key: 'segment', label: '图像分割', icon: Scissors, title: '分割模型：每个像素属于哪一类（POST /api/tools/segment）', run: '开始分割', running: '正在分割…' },
]

/** ?view=tools&tool=gdino|segment */
function initialTool(): ToolKey {
  try {
    const requested = new URLSearchParams(window.location.search).get('tool')
    if (isToolKey(requested)) return requested
  } catch { /* the default tool */ }
  return 'gdino'
}

/**
 * 模型工具: calls the backend's diagnostic model tools on one image — GroundingDINO (the detector of
 * 业务搜图's region search) and the segmentation model. Segmentation asks for the mask only
 * (output_vis=false); the page draws it over the image in several ways.
 */
export function ModelToolsPanel({ apiBase }: { apiBase: string }) {
  const [tool, setTool] = useState<ToolKey>(initialTool)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [dragging, setDragging] = useState(false)
  const [caption, setCaption] = useState('')
  const [boxThreshold, setBoxThreshold] = useState('')
  const [textThreshold, setTextThreshold] = useState('')
  const [detection, setDetection] = useState<DetectionResult | null>(null)
  const [segmentation, setSegmentation] = useState<SegmentResult | null>(null)
  const [busy, setBusy] = useState<ToolKey | null>(null)
  const request = useRef<AbortController | null>(null)
  const ids = { caption: useId(), captionHint: useId(), box: useId(), text: useId(), thresholdHint: useId() }

  useEffect(() => {
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('tool', tool)
      window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
    } catch { /* the tool still changes when the URL cannot be updated */ }
  }, [tool])
  useEffect(() => {
    if (!file) { setPreview(''); return }
    const objectUrl = URL.createObjectURL(file)
    setPreview(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  useEffect(() => () => request.current?.abort(), [])

  const box = thresholdFrom(boxThreshold)
  const text = thresholdFrom(textThreshold)
  const paramsValid = box !== undefined && text !== undefined
  const current = TOOLS.find(item => item.key === tool)!

  function cancel() {
    request.current?.abort()
    request.current = null
    setBusy(null)
  }

  /** A new image: the results of the previous one go. */
  function choose(next: File | undefined) {
    if (!next) return
    if (!IMAGE_TYPES.includes(next.type)) { notify.warn('请选择 JPG、PNG、WebP 或 BMP 格式的图片。', { title: '不能用这张图片' }); return }
    if (next.size === 0 || next.size > MAX_BYTES) { notify.warn('图片大小需大于 0 且不超过 15 MB。', { title: '不能用这张图片' }); return }
    cancel()
    setFile(next)
    setDetection(null)
    setSegmentation(null)
  }

  function clear() {
    cancel()
    setFile(null)
    setDetection(null)
    setSegmentation(null)
  }

  async function run() {
    if (!file || busy || !paramsValid) return
    const which = tool
    const controller = new AbortController()
    request.current = controller
    setBusy(which)
    const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
    try {
      if (which === 'gdino') {
        const result = await detectObjects(file, { caption: captionFrom(caption), boxThreshold: box, textThreshold: text }, { baseUrl: apiBase, signal: controller.signal })
        if (request.current === controller) setDetection(result)
      } else {
        const result = await segmentImage(file, { baseUrl: apiBase, signal: controller.signal })
        if (request.current === controller) setSegmentation(result)
      }
    } catch (cause) {
      if (request.current !== controller) return // cancelled, or the image changed
      const title = which === 'gdino' ? '目标检测失败' : '图像分割失败'
      notify.error(controller.signal.aborted ? '等待超时，请稍后重试。' : cause instanceof Error ? cause.message : '请求失败，请重试。', { title })
    } finally {
      window.clearTimeout(timer)
      if (request.current === controller) { request.current = null; setBusy(null) }
    }
  }

  const view = !preview ? null
    : tool === 'gdino' ? (detection && <DetectionView result={detection} imageUrl={preview} />)
    : (segmentation && <SegmentationView result={segmentation} imageUrl={preview} />)
  return <section className="model-tools" aria-label="模型工具">
    <section className="mt-controls mg-panel" aria-label="调用参数">
      <div className="mt-block">
        <h2>图片</h2>
        <div className={`upload-area ${dragging ? 'is-dragging' : ''} ${file ? 'has-image' : ''}`}
          onDragOver={event => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={event => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]) }}>
          {file && preview ? <>
            <img className="query-preview" src={preview} alt="选中的图片" decoding="async" />
            <div className="selected-file-info">
              <b>{file.name}</b><span>{(file.size / 1024).toFixed(0)} KB · 两个工具共用这张图</span>
              <ImageFilePicker className="replace-image" label="选择图片" onFiles={files => choose(files[0])}><Upload size={13} />更换图片</ImageFilePicker>
            </div>
            <button type="button" className="remove-image" aria-label="移除图片" onClick={clear}><X size={15} /></button>
          </> : <ImageFilePicker className="upload-trigger" label="选择图片" onFiles={files => choose(files[0])}>
            <span className="upload-icon"><UploadCloud size={25} strokeWidth={1.3} /></span>
            <span className="upload-copy"><b>点击上传，或拖拽图片至此</b><small>JPG、PNG、WebP、BMP，不超过 15 MB</small></span>
          </ImageFilePicker>}
        </div>
      </div>

      <div className="mt-block">
        <h2>工具</h2>
        <div className="segmented" aria-label="选择工具">
          {TOOLS.map(({ key, label, icon: Icon, title }) => <button type="button" key={key} title={title}
            className={tool === key ? 'selected' : ''} aria-pressed={tool === key} onClick={() => setTool(key)}>
            <Icon size={14} />{label}
          </button>)}
        </div>
      </div>

      {tool === 'gdino' ? <div className="mt-block mt-params">
        <div className="mt-field">
          <label htmlFor={ids.caption}>检测什么</label>
          <input id={ids.caption} className="input" value={caption} placeholder="如 food . plate . bowl" aria-describedby={ids.captionHint}
            onChange={event => setCaption(event.target.value)} />
          <small id={ids.captionHint}>多个类别用「.」或逗号隔开；留空时用 GROUNDINGDINO_CONFIG.caption。</small>
        </div>
        <div className="mt-thresholds">
          <div className="mt-field">
            <label htmlFor={ids.box}>box_threshold</label>
            <input id={ids.box} className="input" type="number" inputMode="decimal" min={0} max={1} step={0.05} placeholder="配置值"
              value={boxThreshold} aria-invalid={box === undefined} aria-describedby={ids.thresholdHint} onChange={event => setBoxThreshold(event.target.value)} />
          </div>
          <div className="mt-field">
            <label htmlFor={ids.text}>text_threshold</label>
            <input id={ids.text} className="input" type="number" inputMode="decimal" min={0} max={1} step={0.05} placeholder="配置值"
              value={textThreshold} aria-invalid={text === undefined} aria-describedby={ids.thresholdHint} onChange={event => setTextThreshold(event.target.value)} />
          </div>
        </div>
        <small id={ids.thresholdHint} className="mt-hint">box_threshold：框的置信度低于它就不要；text_threshold：caption 里的词和框的匹配度高过它，才算这个框的词。留空用配置值，结果里写着实际用的值。</small>
        {!paramsValid && <p className="mg-form-error" role="alert">阈值要在 0～1 之间。</p>}
      </div> : <div className="mt-block">
        <p className="mt-hint">只向服务端要 mask（output_vis=false），叠加、轮廓、抠图和并排对比都在浏览器里用原图画，切换展示方式不用重新调用。</p>
      </div>}

      <div className="mt-actions">
        <button type="button" className="btn btn-primary" disabled={!file || busy !== null || !paramsValid} onClick={() => void run()}>
          {busy === tool ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}{busy === tool ? current.running : current.run}
        </button>
        {busy && <button type="button" className="text-button" onClick={() => { cancel(); notify.info('已取消这次调用。') }}>取消</button>}
      </div>
    </section>

    <section className="mt-result mg-panel" aria-label={tool === 'gdino' ? '检测结果' : '分割结果'} aria-busy={busy === tool}>
      {busy === tool && view && <p className="mt-loading mt-rerun" role="status"><LoaderCircle size={14} className="spin" aria-hidden />{current.running}</p>}
      {view
        ? view
        : busy === tool ? <p className="mt-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden />{current.running}</p>
        : <EmptyState icon={file ? current.icon : ImageIcon}
            title={file ? `还没有${current.label}结果` : '先选一张图片'}
            description={file
              ? `点「${current.run}」调用 ${tool === 'gdino' ? 'POST /api/tools/gdino' : 'POST /api/tools/segment'}。`
              : '目标检测用 GroundingDINO 按文字在图里找框（和业务搜图的区域搜索是同一个模型），图像分割给出每个像素的类别。'} />}
    </section>
  </section>
}
