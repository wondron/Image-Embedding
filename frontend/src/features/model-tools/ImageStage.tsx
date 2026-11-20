import { useState, type CSSProperties, type ReactNode } from 'react'

/**
 * An image at the size the server decoded it (width × height), with layers over it in that pixel
 * space: a canvas or an SVG with the class mt-layer fills the same box. At most 70% of the
 * window's height.
 */
export function ImageStage({ width, height, imageUrl, alt = '原图', className = '', children }: {
  width: number
  height: number
  /** The original image; none for a stage of layers only (抠图, the mask on its own). */
  imageUrl?: string
  alt?: string
  className?: string
  children?: ReactNode
}) {
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const style: CSSProperties = { aspectRatio: `${width} / ${height}`, maxWidth: `min(100%, calc(70vh * ${width / height}))` }
  // The browser turns a photo by its EXIF orientation; the server may not have: the layers would not line up
  const mismatch = natural !== null && (natural.width !== width || natural.height !== height)
  return <figure className="mt-figure">
    <div className={`mt-stage ${className}`} style={style}>
      {imageUrl && <img src={imageUrl} alt={alt} decoding="async"
        onLoad={event => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />}
      {children}
    </div>
    {mismatch && <figcaption className="mt-warning" role="status">
      浏览器显示的原图是 {natural.width} × {natural.height}，模型看到的是 {width} × {height}（多半是照片里记录的拍摄方向），叠加的位置可能对不上。
    </figcaption>}
  </figure>
}
