import { asText } from './parse'

/** Only remote HTTP(S) sources may become clickable links or image sources. Never path → src. */
export function safeHttpUrl(value: unknown): string | null {
  const candidate = asText(value)
  if (!candidate) return null
  try {
    const url = new URL(candidate.startsWith('//') ? `https:${candidate}` : candidate)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}

/** Safe HTTP(S) URLs of `values`, in order, without duplicates. */
export function uniqueHttpUrls(values: readonly unknown[]): string[] {
  return [...new Set(values.map(safeHttpUrl).filter((url): url is string => url !== null))]
}
