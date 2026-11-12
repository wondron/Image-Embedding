import { useState, type ReactNode } from 'react'
import { useApiBase } from '../../shared/api'
import { markHostBroken, storedImageSources, type StoredImageVariant } from './imageSources'

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
  const sources = storedImageSources([url], id, variant, apiBase ?? contextBase)
  const key = sources.join('\n')
  const [failed, setFailed] = useState<{ key: string; count: number }>({ key, count: 0 })
  const index = failed.key === key ? failed.count : 0
  const src = sources[index]
  if (!src) return <>{placeholder}</>
  return <img
    key={src}
    src={src}
    alt={alt}
    className={className}
    loading={loading}
    decoding="async"
    referrerPolicy="no-referrer"
    onLoad={() => { if (index > 0 && url && sources[0] === url) markHostBroken(url) }}
    onError={() => setFailed({ key, count: index + 1 })}
  />
}
