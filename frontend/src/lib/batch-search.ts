export const maxBatchImages = 30
export const maxBatchImageBytes = 15 * 1024 * 1024

const acceptedTypes = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/bmp'])

export function batchFileKey(file: File): string {
  return JSON.stringify([file.name, file.size, file.lastModified, file.type])
}

/** Validate before creating previews; duplicate and rejected files never consume queue slots. */
export function selectBatchFiles(incoming: File[], existing: File[]): { files: File[]; messages: string[] } {
  const known = new Set(existing.map(batchFileKey))
  const files: File[] = []
  const messages: string[] = []
  let duplicates = 0
  let overLimit = 0
  for (const file of incoming) {
    if (!acceptedTypes.has(file.type)) {
      messages.push(`${file.name}：请选择 JPG、PNG、WebP 或 BMP 图片。`)
      continue
    }
    if (!file.size || file.size > maxBatchImageBytes) {
      messages.push(`${file.name}：图片大小需大于 0 且不超过 15 MB。`)
      continue
    }
    const key = batchFileKey(file)
    if (known.has(key)) { duplicates++; continue }
    known.add(key)
    if (existing.length + files.length >= maxBatchImages) { overLimit++; continue }
    files.push(file)
  }
  if (duplicates) messages.push(`已忽略 ${duplicates} 张重复图片。`)
  if (overLimit) messages.push(`每批最多 ${maxBatchImages} 张，另有 ${overLimit} 张未加入。`)
  return { files, messages }
}
