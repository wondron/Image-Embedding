/**
 * An SVG picture as a PNG, drawn through a canvas in the browser (the topology diagram, for places
 * that do not take SVG: chat, slides). The SVG must carry its own width / height and no external
 * resource, or the canvas cannot be read back.
 */

/** Chrome allows 32767 px a side and about 268 M px in all; other browsers less. Stay well inside. */
const MAX_SIDE = 16000
const MAX_AREA = 100_000_000
/** Twice the CSS size when it fits, for sharp text. */
const PREFERRED_SCALE = 2
/** Below this the text is no longer readable: better no PNG than a useless one. */
const MIN_SCALE = 0.5

export interface RasterResult {
  blob: Blob
  /** Pixels per CSS pixel of the SVG: 2 when it fits, less for a very tall diagram. */
  scale: number
  width: number
  height: number
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('浏览器没能把拓扑图画出来，请改为下载 SVG。'))
    image.src = url
  })
}

/** The scale a width × height picture is drawn at; 0 when it cannot be drawn readably. */
export function rasterScale(width: number, height: number): number {
  if (!(width > 0 && height > 0)) return 0
  const scale = Math.min(PREFERRED_SCALE, MAX_SIDE / width, MAX_SIDE / height, Math.sqrt(MAX_AREA / (width * height)))
  return scale >= MIN_SCALE ? scale : 0
}

/** Throws an Error whose message is ready to show (the diagram is too large, or the canvas refused). */
export async function svgToPng(svg: string, width: number, height: number): Promise<RasterResult> {
  const scale = rasterScale(width, height)
  if (!scale) throw new Error('类别太多，整张图超出了浏览器能导出的 PNG 尺寸；请下载 SVG（矢量图，可任意放大）。')
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = await loadImage(url)
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('这个浏览器不支持画布导出，请改为下载 SVG。')
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'))
    if (!blob) throw new Error('PNG 生成失败（图片可能太大），请改为下载 SVG。')
    return { blob, scale, width: canvas.width, height: canvas.height }
  } finally {
    URL.revokeObjectURL(url)
  }
}
