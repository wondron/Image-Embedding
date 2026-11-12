import type { ReactNode } from 'react'
import { LoaderCircle } from 'lucide-react'
import { useImageCascade } from './useImageCascade'
import './progressive-image.css'

export interface ProgressiveImageProps {
  /** Quick to load and usually cached already (the thumbnail the list showed), in order of preference. */
  previewSources: readonly string[]
  /** The full image (the original), in order of preference: shown over the preview once loaded. */
  fullSources: readonly string[]
  /** What the image shows, for assistive technology. */
  alt: string
  className?: string
  /** Shown when neither could be loaded. */
  placeholder?: ReactNode
}

/**
 * A detail view's image: the preview at once, the original over it as soon as it has loaded
 * (「正在加载原图」 meanwhile). Lists load thumbnails only; the original is fetched here, when
 * someone opens the image. When the original cannot be loaded the preview stays and says so.
 */
export function ProgressiveImage({ previewSources, fullSources, alt, className = '', placeholder = null }: ProgressiveImageProps) {
  const preview = useImageCascade(previewSources)
  const full = useImageCascade(fullSources)
  if (!full.src && !preview.src) return <>{placeholder}</>
  const fullFailed = fullSources.length > 0 && full.src === null
  return <div className={`progressive-image ${className}`.trim()} role="img" aria-label={alt}>
    {preview.src && !full.loaded && <img
      key={`preview:${preview.src}`}
      className="progressive-image-preview"
      src={preview.src}
      alt=""
      decoding="async"
      referrerPolicy="no-referrer"
      onLoad={preview.onLoad}
      onError={preview.onError}
    />}
    {full.src && <img
      key={`full:${full.src}`}
      className={`progressive-image-full${full.loaded ? ' is-loaded' : ''}`}
      src={full.src}
      alt=""
      decoding="async"
      referrerPolicy="no-referrer"
      onLoad={full.onLoad}
      onError={full.onError}
    />}
    {full.src && !full.loaded && <span className="progressive-image-status" role="status">
      <LoaderCircle size={13} className="spin" aria-hidden="true" />正在加载原图
    </span>}
    {fullFailed && preview.src && <span className="progressive-image-status is-error">原图加载失败，显示的是缩略图</span>}
  </div>
}
