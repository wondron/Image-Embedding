import { useState } from 'react'
import { isStoredImageUrl, markHostBroken } from './imageSources'

export interface ImageCascade {
  /** The source to show now; null when every one has failed, or there is none. */
  src: string | null
  /** The source shown now has loaded. */
  loaded: boolean
  onLoad: () => void
  onError: () => void
}

/**
 * Walk `sources` in order, moving on at each load error. What was learnt is kept per URL, so a
 * list recomputed on every render (or one that drops URLs since found failing) does not start
 * over. When the API's copy loads after an image's own URLs failed, their hosts are remembered
 * as failing (markHostBroken): later images go to the API first.
 */
export function useImageCascade(sources: readonly string[]): ImageCascade {
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set())
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null)
  const index = sources.findIndex(source => !failed.has(source))
  const src = index < 0 ? null : sources[index]
  return {
    src,
    loaded: src !== null && src === loadedSrc,
    onLoad: () => {
      if (src === null) return
      setLoadedSrc(src)
      if (index > 0 && isStoredImageUrl(src)) {
        sources.slice(0, index).filter(source => !isStoredImageUrl(source)).forEach(markHostBroken)
      }
    },
    onError: () => {
      if (src !== null) setFailed(current => new Set(current).add(src))
    },
  }
}
