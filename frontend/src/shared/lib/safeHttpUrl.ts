/** Only remote HTTP(S) sources may become clickable links or image sources. Never path → src. */

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function safeHttpUrl(value: unknown): string | null {
  const candidate = text(value)
  if (!candidate) return null
  try {
    const url = new URL(candidate.startsWith('//') ? `https:${candidate}` : candidate)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}