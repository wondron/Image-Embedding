import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { LoaderCircle } from 'lucide-react'
import { ImageStage } from './ImageStage'
import {
  PALETTE, classColor, hexToRgb, maskValues, outlineRadius, paintCutout, paintMask, paintOutline, summarize,
  type MaskSummary, type MaskValues, type Rgb, type Rgba,
} from './mask'
import type { SegmentResult } from './model'

type Mode = 'overlay' | 'outline' | 'cutout' | 'side'
const MODES: ReadonlyArray<{ key: Mode; label: string; title: string }> = [
  { key: 'overlay', label: '叠加', title: 'mask 半透明地盖在原图上' },
  { key: 'outline', label: '轮廓', title: '只画每块区域的边界' },
  { key: 'cutout', label: '抠图', title: '只留前景处的原图像素' },
  { key: 'side', label: '并排', title: '原图和 mask 左右对比' },
]
const DEFAULT_COLOR = '#22c55e'
/** 并排: the mask on its own, on a dark background. */
const MASK_BACKGROUND: Rgba = [15, 23, 42, 255]
type Pixels = Uint8ClampedArray<ArrayBuffer>

const percent = (ratio: number) => `${(ratio * 100).toFixed(1)}%`

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('the image cannot be decoded'))
    image.src = src
  })
}

/** RGBA of an image drawn at width × height without smoothing (the mask is already that size). */
function pixelsOf(image: HTMLImageElement, width: number, height: number): Pixels {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('canvas 2d is not available')
  context.imageSmoothingEnabled = false
  context.drawImage(image, 0, 0, width, height)
  return context.getImageData(0, 0, width, height).data
}

function RgbaCanvas({ data, width, height, label, style }: { data: Pixels; width: number; height: number; label: string; style?: CSSProperties }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    canvas.width = width
    canvas.height = height
    canvas.getContext('2d')?.putImageData(new ImageData(data, width, height), 0, 0)
  }, [data, width, height])
  return <canvas ref={ref} className="mt-layer" role="img" aria-label={label} style={style} />
}

function Legend({ summary, first }: { summary: MaskSummary; first: Rgb }) {
  if (summary.kind === 'probability') {
    return <p className="mt-legend-note">这是概率图（模型只有一个输出通道）：颜色越实，概率越高；前景按概率 ≥ 0.5 统计。</p>
  }
  if (!summary.classes.length) return <p className="mt-empty-note" role="status">mask 里没有前景：模型认为整张图都是背景。</p>
  return <ul className="mt-legend" aria-label="mask 里的类别">
    {summary.classes.map(item => <li key={item.value}>
      <span className="mt-swatch" style={{ '--c': classColor(item.value, first).join(' ') } as CSSProperties} />
      类别 {item.value}<b>{percent(item.ratio)}</b>
    </li>)}
  </ul>
}

/**
 * The segment tool's mask, drawn here over the original (the request asks for the mask only):
 * 叠加 (adjustable opacity), 轮廓, 抠图 (the foreground's own pixels) and 并排 (original | mask).
 */
export function SegmentationView({ result, imageUrl }: { result: SegmentResult; imageUrl: string }) {
  const { width, height } = result
  const [mode, setMode] = useState<Mode>('overlay')
  const [opacity, setOpacity] = useState(0.5)
  const [color, setColor] = useState(DEFAULT_COLOR)
  const [decoded, setDecoded] = useState<{ values: MaskValues; image: Pixels } | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let current = true
    setDecoded(null)
    setFailed(false)
    Promise.all([loadImage(result.maskUrl), loadImage(imageUrl)]).then(([mask, original]) => {
      if (current) setDecoded({ values: maskValues(pixelsOf(mask, width, height)), image: pixelsOf(original, width, height) })
    }).catch(() => { if (current) setFailed(true) })
    return () => { current = false }
  }, [result.maskUrl, imageUrl, width, height])

  const summary = useMemo(() => (decoded ? summarize(decoded.values) : null), [decoded])
  const kind = summary?.kind ?? 'classes'
  const first: Rgb = hexToRgb(color) ?? PALETTE[0]
  const layer = useMemo(() => {
    if (!decoded) return null
    const tint = hexToRgb(color) ?? PALETTE[0]
    switch (mode) {
      case 'overlay': return paintMask(decoded.values, kind, tint)
      case 'outline': return paintOutline(decoded.values, width, height, kind, tint, outlineRadius(width, height))
      case 'cutout': return paintCutout(decoded.values, kind, decoded.image)
      case 'side': return paintMask(decoded.values, kind, tint, MASK_BACKGROUND)
    }
  }, [decoded, mode, kind, color, width, height])

  let stage
  if (failed) stage = <p className="mg-form-error" role="alert">mask 或原图没能在浏览器里解码，请换一张图片再试。</p>
  else if (!layer) stage = <p className="mt-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden />正在绘制 mask…</p>
  else if (mode === 'side') {
    stage = <div className="mt-side">
      <ImageStage width={width} height={height} imageUrl={imageUrl} alt="原图" />
      <ImageStage width={width} height={height}><RgbaCanvas data={layer} width={width} height={height} label="mask" /></ImageStage>
    </div>
  } else if (mode === 'cutout') {
    stage = <ImageStage width={width} height={height} className="mt-checker"><RgbaCanvas data={layer} width={width} height={height} label="抠出的前景" /></ImageStage>
  } else {
    stage = <ImageStage width={width} height={height} imageUrl={imageUrl} alt="分割的图片">
      <RgbaCanvas data={layer} width={width} height={height}
        label={mode === 'overlay' ? '叠加的 mask' : 'mask 的轮廓'} style={mode === 'overlay' ? { opacity } : undefined} />
    </ImageStage>
  }

  return <div className="mt-view">
    <dl className="mt-summary">
      <div><dt>前景</dt><dd>{summary ? percent(summary.foreground) : '…'}</dd></div>
      {summary?.kind === 'classes' && summary.classes.length > 1 && <div><dt>类别</dt><dd>{summary.classes.length}</dd></div>}
      <div><dt>图片</dt><dd>{width} × {height}</dd></div>
      <div><dt>耗时</dt><dd>{(result.latencyMs / 1000).toFixed(2)} s</dd></div>
    </dl>
    <div className="mt-view-toolbar">
      <div className="segmented mt-modes" aria-label="展示方式">
        {MODES.map(item => <button type="button" key={item.key} title={item.title}
          className={mode === item.key ? 'selected' : ''} aria-pressed={mode === item.key}
          onClick={() => setMode(item.key)}>{item.label}</button>)}
      </div>
      {mode === 'overlay' && <label className="mt-range">不透明度
        <input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={event => setOpacity(Number(event.target.value))} />
        <output>{Math.round(opacity * 100)}%</output>
      </label>}
      {mode !== 'cutout' && <label className="mt-color">颜色<input type="color" value={color} onChange={event => setColor(event.target.value)} /></label>}
    </div>
    {stage}
    {summary && <Legend summary={summary} first={first} />}
  </div>
}
