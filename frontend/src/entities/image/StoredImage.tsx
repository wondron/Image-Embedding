import type { ReactNode } from 'react'
import { useApiBase } from '../../shared/api'
import { storedImageSources, type StoredImageVariant } from './imageSources'
import { useImageCascade } from './useImageCascade'

export interface StoredImageProps {
  /** The image's library id: when `url` cannot be loaded, the API serves the stored file. */
  id: string | null
  url: string | null
  variant?: StoredImageVariant
  alt?: string
  className?: string
  /** Shown when nothing could be loaded. */
  placeholder?: ReactNode
  loading?: 'lazy' | 'eager'
  /** Defaults to the app's (ApiBaseContext). */
  apiBase?: string
}

/**
 * An image of the library: its own URL first, then the API's copy (GET /api/images/{id}/file),
 * then the placeholder. When the API's copy loads after the URL failed, the URL's host is
 * remembered as failing and later images skip it.
 */
export function StoredImage({ id, url, variant = 'thumb', alt = '', className, placeholder = null, loading = 'lazy', apiBase }: StoredImageProps) {
  const contextBase = useApiBase()
  const image = useImageCascade(storedImageSources([url], id, variant, apiBase ?? contextBase))
  if (!image.src) return <>{placeholder}</>
  return <img
    key={image.src}
    src={image.src}
    alt={alt}
    className={className}
    loading={loading}
    decoding="async"
    referrerPolicy="no-referrer"
    onLoad={image.onLoad}
    onError={image.onError}
  />
}
