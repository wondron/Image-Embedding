import { useEffect, useId, useRef, useState, type ChangeEvent } from 'react'
import { ChefHat, Crosshair, ImageIcon, LoaderCircle, Play, Scissors, Upload, UploadCloud, X, type LucideIcon } from 'lucide-react'
import { ImageFilePicker } from '../../components/ImageFilePicker'
import { EmptyState, notify } from '../../shared/ui'
import { detectObjects, recognizeRecipes, segmentImage } from './api'
import { DetectionView } from './DetectionView'
import { RecipeView } from './RecipeView'
import { SegmentationView } from './SegmentationView'
import {
  MAX_REGIONS_LIMIT, captionFrom, isToolKey, maxRegionsFrom, thresholdFrom,
  type DetectionResult, type RecipeResult, type SegmentResult, type ToolKey,
} from './model'
import './model-tools.css'

const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp']
const MAX_BYTES = 15 * 1024 * 1024
/** Same as 图搜图: a loaded GPU can take far longer than the usual second or two. */
const TIMEOUT_MS = 120_000

interface Tool {
  key: ToolKey
  label: string
  icon: LucideIcon
  title: string
  endpoint: string
  run: string
  running: string
  failed: string
  /** The result panel's name. */
  result: string
}

const TOOLS: readonly Tool[] = [
  { key: 'gdino', label: '目标检测', icon: Crosshair, title: 'GroundingDINO：按文字在图里找目标（POST /api/tools/gdino）', endpoint: 'POST /api/tools/gdino', run: '开始检测', running: '正在检测…', failed: '目标检测失败', result: '检测结果' },
  { key: 'segment', label: '图像分割', icon: Scissors, title: '分割模型：每个像素属于哪一类（POST /api/tools/segment）', endpoint: 'POST /api/tools/segment', run: '开始分割', running: '正在分割…', failed: '图像分割失败', result: '分割结果' },
  { key: 'recipe', label: '菜谱识别', icon: ChefHat, title: '业务搜图的菜谱识别：GroundingDINO 找框，每个框交给菜谱识别模型（POST /api/tools/recipe）', endpoint: 'POST /api/tools/recipe', run: '开始识别', running: '正在识别…', failed: '菜谱识别失败', result: '识别结果' },
]

/** The detection parameters as typed: blank means the configured value. */
interface DetectDraft {
  caption: string
  box: string
  text: string
  maxRegions: string
}

const BLANK_DRAFT: DetectDraft = { caption: '', box: '', text: '', maxRegions: '' }

/** A draft read: undefined where a value is typed wrong. */
function readDraft(draft: DetectDraft) {
  return {
    caption: captionFrom(draft.caption),
    boxThreshold: thresholdFrom(draft.box),
    textThreshold: thresholdFrom(draft.text),
    maxRegions: maxRegionsFrom(draft.maxRegions),
  }
}

/** ?view=tools&tool=gdino|segment|recipe */
function initialTool(): ToolKey {
  try {
    const requested = new URLSearchParams(window.location.search).get('tool')
    if (isToolKey(requested)) return requested
  } catch { /* the default tool */ }
  return 'gdino'
}

/**
 * What GroundingDINO is asked: the caption, its two thresholds and, for 菜谱识别, how many boxes.
 * Blank fields take `section`'s values (GROUNDINGDINO_CONFIG for 目标检测, AUTO_SEARCH_REGION_CONFIG —
 * what 业务搜图 detects with — for 菜谱识别).
 */
function DetectFields({ draft, onChange, section, withMaxRegions }: {
  draft: DetectDraft
  onChange: (draft: DetectDraft) => void
  section: string
  withMaxRegions?: boolean
}) {
  const ids = { caption: useId(), captionHint: useId(), box: useId(), text: useId(), maxRegions: useId(), thresholdHint: useId() }
  const read = readDraft(draft)
  const set = (key: keyof DetectDraft) => (event: ChangeEvent<HTMLInputElement>) => onChange({ ...draft, [key]: event.target.value })
  return <div className="mt-block mt-params">
    <div className="mt-field">
      <label htmlFor={ids.caption}>检测什么</label>
      <input id={ids.caption} className="input" value={draft.caption} placeholder="如 food . plate . bowl" aria-describedby={ids.captionHint} onChange={set('caption')} />
      <small id={ids.captionHint}>多个类别用「.」或逗号隔开；留空时用 {section}.caption{withMaxRegions ? '（业务搜图检测用的）' : ''}。</small>
    </div>
    <div className="mt-thresholds">
      <div className="mt-field">
        <label htmlFor={ids.box}>box_threshold</label>
        <input id={ids.box} className="input" type="number" inputMode="decimal" min={0} max={1} step={0.05} placeholder="配置值"
          value={draft.box} aria-invalid={read.boxThreshold === undefined} aria-describedby={ids.thresholdHint} onChange={set('box')} />
      </div>
      <div className="mt-field">
        <label htmlFor={ids.text}>text_threshold</label>
        <input id={ids.text} className="input" type="number" inputMode="decimal" min={0} max={1} step={0.05} placeholder="配置值"
          value={draft.text} aria-invalid={read.textThreshold === undefined} aria-describedby={ids.thresholdHint} onChange={set('text')} />
      </div>
      {withMaxRegions && <div className="mt-field">
        <label htmlFor={ids.maxRegions}>max_regions</label>
        <input id={ids.maxRegions} className="input" type="number" inputMode="numeric" min={1} max={MAX_REGIONS_LIMIT} step={1} placeholder="配置值"
          value={draft.maxRegions} aria-invalid={read.maxRegions === undefined} aria-describedby={ids.thresholdHint} onChange={set('maxRegions')} />
      </div>}
    </div>
    <small id={ids.thresholdHint} className="mt-hint">
      box_threshold：框的置信度低于它就不要；text_threshold：caption 里的词和框的匹配度高过它，才算这个框的词。
      {withMaxRegions && 'max_regions：最多识别几个框（按置信度从高到低，边长小于 16 像素的框不算）。菜谱阈值在结果里调。'}
      留空用配置值，结果里写着实际用的值。
    </small>
    {(read.boxThreshold === undefined || read.textThreshold === undefined) && <p className="mg-form-error" role="alert">阈值要在 0～1 之间。</p>}
    {withMaxRegions && read.maxRegions === undefined && <p className="mg-form-error" role="alert">max_regions 要是 1～{MAX_REGIONS_LIMIT} 的整数。</p>}
  </div>
}

/**
 * 模型工具: calls the backend's diagnostic model tools on one image — GroundingDINO (the detector of
 * 业务搜图's region search and 菜谱识别), the segmentation model, and 业务搜图's 菜谱识别 itself, box
 * by box. Segmentation asks for the mask only (output_vis=false); the page draws it over the image
 * in several ways. `initialFile`: the image to start with (a 业务搜图 query opened here).
 */
export function ModelToolsPanel({ apiBase, initialFile = null }: { apiBase: string; initialFile?: File | null }) {
  const [tool, setTool] = useState<ToolKey>(initialTool)
  const [file, setFile] = useState<File | null>(initialFile)
  const [preview, setPreview] = useState('')
  const [dragging, setDragging] = useState(false)
  const [gdinoDraft, setGdinoDraft] = useState<DetectDraft>(BLANK_DRAFT)
  const [recipeDraft, setRecipeDraft] = useState<DetectDraft>(BLANK_DRAFT)
  const [detection, setDetection] = useState<DetectionResult | null>(null)
  const [segmentation, setSegmentation] = useState<SegmentResult | null>(null)
  const [recipe, setRecipe] = useState<RecipeResult | null>(null)
  /** The recipe threshold tried on the results; null: the configured one. Kept across images. */
  const [recipeThreshold, setRecipeThreshold] = useState<number | null>(null)
  const [busy, setBusy] = useState<ToolKey | null>(null)
  const request = useRef<AbortController | null>(null)

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

  const gdinoParams = readDraft(gdinoDraft)
  const recipeParams = readDraft(recipeDraft)
  const paramsValid = tool === 'gdino'
    ? gdinoParams.boxThreshold !== undefined && gdinoParams.textThreshold !== undefined
    : tool === 'recipe'
      ? recipeParams.boxThreshold !== undefined && recipeParams.textThreshold !== undefined && recipeParams.maxRegions !== undefined
      : true
  const current = TOOLS.find(item => item.key === tool)!

  function cancel() {
    request.current?.abort()
    request.current = null
    setBusy(null)
  }

  function forgetResults() {
    setDetection(null)
    setSegmentation(null)
    setRecipe(null)
  }

  /** A new image: the results of the previous one go. */
  function choose(next: File | undefined) {
    if (!next) return
    if (!IMAGE_TYPES.includes(next.type)) { notify.warn('请选择 JPG、PNG、WebP 或 BMP 格式的图片。', { title: '不能用这张图片' }); return }
    if (next.size === 0 || next.size > MAX_BYTES) { notify.warn('图片大小需大于 0 且不超过 15 MB。', { title: '不能用这张图片' }); return }
    cancel()
    setFile(next)
    forgetResults()
  }

  function clear() {
    cancel()
    setFile(null)
    forgetResults()
  }

  async function run() {
    if (!file || busy || !paramsValid) return
    const which = tool
    const controller = new AbortController()
    request.current = controller
    setBusy(which)
    const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
    const options = { baseUrl: apiBase, signal: controller.signal }
    try {
      if (which === 'gdino') {
        const { caption, boxThreshold, textThreshold } = gdinoParams
        const result = await detectObjects(file, { caption, boxThreshold, textThreshold }, options)
        if (request.current === controller) setDetection(result)
      } else if (which === 'recipe') {
        const result = await recognizeRecipes(file, recipeParams, options)
        if (request.current === controller) setRecipe(result)
      } else {
        const result = await segmentImage(file, options)
        if (request.current === controller) setSegmentation(result)
      }
    } catch (cause) {
      if (request.current !== controller) return // cancelled, or the image changed
      notify.error(controller.signal.aborted ? '等待超时，请稍后重试。' : cause instanceof Error ? cause.message : '请求失败，请重试。', { title: TOOLS.find(item => item.key === which)!.failed })
    } finally {
      window.clearTimeout(timer)
      if (request.current === controller) { request.current = null; setBusy(null) }
    }
  }

  const view = !preview ? null
    : tool === 'gdino' ? (detection && <DetectionView result={detection} imageUrl={preview} />)
    : tool === 'recipe' ? (recipe && <RecipeView result={recipe} imageUrl={preview} threshold={recipeThreshold} onThresholdChange={setRecipeThreshold} />)
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
              <b>{file.name}</b><span>{(file.size / 1024).toFixed(0)} KB · 三个工具共用这张图</span>
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

      {tool === 'gdino' && <DetectFields draft={gdinoDraft} onChange={setGdinoDraft} section="GROUNDINGDINO_CONFIG" />}
      {tool === 'recipe' && <>
        <div className="mt-block">
          <p className="mt-hint">和业务搜图的菜谱识别一样：GroundingDINO 在图里找框（参数取 AUTO_SEARCH_REGION_CONFIG），每个框裁出来交给菜谱识别模型，和底库最相近的菜相似度高于阈值才算识别出这道菜。另附整张图的识别结果作对比。</p>
        </div>
        <DetectFields draft={recipeDraft} onChange={setRecipeDraft} section="AUTO_SEARCH_REGION_CONFIG" withMaxRegions />
      </>}
      {tool === 'segment' && <div className="mt-block">
        <p className="mt-hint">只向服务端要 mask（output_vis=false），叠加、轮廓、抠图和并排对比都在浏览器里用原图画，切换展示方式不用重新调用。</p>
      </div>}

      <div className="mt-actions">
        <button type="button" className="btn btn-primary" disabled={!file || busy !== null || !paramsValid} onClick={() => void run()}>
          {busy === tool ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}{busy === tool ? current.running : current.run}
        </button>
        {busy && <button type="button" className="text-button" onClick={() => { cancel(); notify.info('已取消这次调用。') }}>取消</button>}
      </div>
    </section>

    <section className="mt-result mg-panel" aria-label={current.result} aria-busy={busy === tool}>
      {busy === tool && view && <p className="mt-loading mt-rerun" role="status"><LoaderCircle size={14} className="spin" aria-hidden />{current.running}</p>}
      {view
        ? view
        : busy === tool ? <p className="mt-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden />{current.running}</p>
        : <EmptyState icon={file ? current.icon : ImageIcon}
            title={file ? `还没有${current.label}结果` : '先选一张图片'}
            description={file
              ? `点「${current.run}」调用 ${current.endpoint}。`
              : '目标检测用 GroundingDINO 按文字在图里找框（和业务搜图的区域搜索是同一个模型），图像分割给出每个像素的类别，菜谱识别按业务搜图的做法给每个检测框识别菜名。'} />}
    </section>
  </section>
}
