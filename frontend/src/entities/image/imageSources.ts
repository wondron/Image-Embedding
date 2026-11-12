import { apiRoot } from '../../shared/api'

/** Which stored file: the thumbnail (the server gives the original when there is none) or the original. */
export type StoredImageVariant = 'thumb' | 'original'

/**
 * GET /api/images/{id}/file: the stored file read back through the API, for when its own URL
 * cannot be loaded (the mock storage's placeholder host, a private bucket, a CDN that is down).
 */
export function storedImageUrl(apiBase: string, id: string, variant: StoredImageVariant): string {
  return `${apiRoot(apiBase)}/api/images/${encodeURIComponent(id)}/file?variant=${variant}`
}

/**
 * Hosts whose image URLs failed while the API's copy of the same image loaded: the URLs are what
 * is wrong there, so later images go to the API first instead of waiting for each to fail. For
 * this page load only.
 */
const brokenHosts = new Set<string>()

function hostOf(url: string): string | null {
  try {
    return new URL(url, typeof window === 'undefined' ? 'http://localhost/' : window.location.href).host
  } catch {
    return null
  }
}

export function markHostBroken(url: string): void {
  const host = hostOf(url)
  if (host) brokenHosts.add(host)
}

export function isHostBroken(url: string): boolean {
  const host = hostOf(url)
  return host !== null && brokenHosts.has(host)
}

/** Tests only. */
export function forgetBrokenHosts(): void {
  brokenHosts.clear()
}

/**
 * Where to load an image from, in order: its own URLs (unless their host is known to fail), then
 * the API's copy when the image has an id in the library.
 */
export function storedImageSources(
  urls: readonly (string | null | undefined)[],
  id: string | null,
  variant: StoredImageVariant,
  apiBase: string,
): string[] {
  const sources = urls.filter((url): url is string => Boolean(url) && !isHostBroken(url!))
  if (id) sources.push(storedImageUrl(apiBase, id, variant))
  return [...new Set(sources)]
}
