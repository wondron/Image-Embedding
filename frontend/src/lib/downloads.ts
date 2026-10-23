import { strToU8, zip, type Zippable } from 'fflate'
import type { SearchImage } from '../types'
import { getImageSources, safeHttpUrl } from './search'

export const MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

// A small structural interface keeps directory saving testable and avoids global declarations.
export interface DownloadDirectory {
  name: string
  getDirectoryHandle(name: string, options?: { create?: boolean }): Promise<DownloadDirectory>
  getFileHandle(name: string, options: { create: boolean }): Promise<{
    createWritable(): Promise<{ write(data: Uint8Array): Promise<void>; close(): Promise<void>; abort(): Promise<void> }>
  }>
}
type PickerWindow = Window & { showDirectoryPicker?: (options: { mode: 'readwrite' }) => Promise<DownloadDirectory> }

export function canChooseDownloadDirectory(): boolean {
  return typeof window !== 'undefined' && window.isSecureContext === true
    && typeof (window as PickerWindow).showDirectoryPicker === 'function'
}

/** Call directly from a click handler, before any other awaited operation. */
export function chooseDownloadDirectory(): Promise<DownloadDirectory> {
  if (!canChooseDownloadDirectory()) return Promise.reject(new Error('此浏览器暂不支持选择目录，请使用 ZIP 下载。'))
  return (window as PickerWindow).showDirectoryPicker!({ mode: 'readwrite' })
}

export interface DownloadFailure { item: SearchImage; reason: string }
export interface DownloadRecord {
  id: string
  title: string
  filename: string
  source: string
  originalUrl: string | null
  thumbnailUrl: string | null
  pageUrl: string | null
  similarity: number | null
  bytes: number
}
export interface DownloadProgress {
  phase: 'fetching' | 'packing' | 'complete'
  total: number
  processed: number
  prepared: number
  saved: number
  failed: number
  bytes: number
  current: string
}
export interface DownloadReport {
  cancelled: boolean
  saved: number
  prepared: number
  total: number
  failures: DownloadFailure[]
  destination: string | null
  records: DownloadRecord[]
  error?: string
}
interface DownloadOptions {
  items: SearchImage[]
  mode: 'zip' | 'folder'
  directory?: DownloadDirectory | null
  signal: AbortSignal
  onProgress?: (progress: DownloadProgress) => void
  saveArchive?: (bytes: Uint8Array, filename: string) => void
  apiBase?: string
}

function abortError() { return new DOMException('已取消下载', 'AbortError') }
function checkAbort(signal: AbortSignal) { if (signal.aborted) throw abortError() }
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : '未知错误' }

function safeName(title: string): string {
  return title.normalize('NFKC').replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_')
    .replace(/[. ]+$/g, '').trim().slice(0, 70) || 'image'
}

const extensions: Record<string, string> = {
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
  'image/avif': 'avif', 'image/bmp': 'bmp', 'image/svg+xml': 'svg', 'image/tiff': 'tiff',
  'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico',
}
function imageExtension(bytes: Uint8Array, contentType: string): string {
  // Prefer file signatures over URL suffixes, which often lie on image CDNs.
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpg'
  if (bytes[0] === 0x89 && String.fromCharCode(...bytes.slice(1, 4)) === 'PNG') return 'png'
  if (String.fromCharCode(...bytes.slice(0, 3)) === 'GIF') return 'gif'
  if (String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP') return 'webp'
  if (bytes[0] === 0x42 && bytes[1] === 0x4d) return 'bmp'
  const type = contentType.split(';')[0].trim().toLowerCase()
  if (extensions[type]) return extensions[type]
  throw new Error('源站未返回支持的图片格式')
}

async function readLimited(response: Response, limit: number, signal: AbortSignal): Promise<Uint8Array> {
  const declared = Number(response.headers.get('Content-Length'))
  if (declared > limit) { await response.body?.cancel(); throw new Error('超过本次下载 200 MB 容量上限') }
  if (!response.body) throw new Error('图片内容为空')
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      checkAbort(signal)
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > limit) throw new Error('超过本次下载 200 MB 容量上限')
      chunks.push(value)
    }
  } catch (error) { await reader.cancel().catch(() => {}); throw error }
  finally { reader.releaseLock() }
  checkAbort(signal)
  if (!length) throw new Error('图片内容为空')
  const data = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength }
  return data
}

async function fetchImage(item: SearchImage, signal: AbortSignal, limit: number, apiBase?: string) {
  const sources = getImageSources(item)
  if (!sources.length) throw new Error('没有有效的图片地址')
  let reason = '图片下载失败'
  const attempts = sources.flatMap(source => [
    { source, requestUrl: source, proxy: false },
    ...(apiBase !== undefined ? [{ source, requestUrl: `${apiBase.replace(/\/+$/, '')}/api/images/download?url=${encodeURIComponent(source)}`, proxy: true }] : []),
  ])
  for (const { source, requestUrl, proxy } of attempts) {
    checkAbort(signal)
    const request = new AbortController()
    const abort = () => request.abort()
    signal.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => request.abort(), 30_000)
    try {
      const response = await fetch(requestUrl, { mode: 'cors', credentials: 'omit', signal: request.signal })
      if (!response.ok) {
        await response.body?.cancel()
        throw new Error(proxy && [400, 422].includes(response.status)
          ? `此图片地址不受下载服务支持（HTTP ${response.status}）` : `HTTP ${response.status}`)
      }
      const bytes = await readLimited(response, limit, request.signal)
      return { bytes, source, extension: imageExtension(bytes, response.headers.get('Content-Type') ?? '') }
    } catch (error) {
      checkAbort(signal)
      reason = request.signal.aborted ? '图片下载超时（30 秒）' : errorMessage(error)
      if (reason === 'Failed to fetch') reason = '源站限制跨域访问或网络不可用'
    } finally { clearTimeout(timer); signal.removeEventListener('abort', abort) }
  }
  throw new Error(reason)
}

function sessionName() {
  // randomUUID is absent on ordinary HTTP LAN previews; ZIP remains usable there.
  const random = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID().slice(0, 8)
    : Math.random().toString(36).slice(2, 10).padEnd(8, '0')
  return `images-${new Date().toISOString().replace(/[:.]/g, '-')}-${random}`
}

async function createSessionDirectory(directory: DownloadDirectory, name: string) {
  for (let index = 0; index < 100; index++) {
    const candidate = index ? `${name}-${index}` : name
    try { await directory.getDirectoryHandle(candidate) }
    catch (error) {
      if (error instanceof Error && error.name === 'NotFoundError') {
        return directory.getDirectoryHandle(candidate, { create: true })
      }
      throw error
    }
  }
  throw new Error('无法创建独立的下载目录，请重新选择目录')
}

async function writeFile(directory: DownloadDirectory, name: string, bytes: Uint8Array, signal: AbortSignal) {
  checkAbort(signal)
  const handle = await directory.getFileHandle(name, { create: true })
  const writer = await handle.createWritable()
  try { checkAbort(signal); await writer.write(bytes); checkAbort(signal); await writer.close() }
  catch (error) { await writer.abort().catch(() => {}); throw error }
}

function makeArchive(files: Zippable, signal: AbortSignal): Promise<Uint8Array> {
  checkAbort(signal)
  return new Promise((resolve, reject) => {
    const cancel = zip(files, { level: 0 }, (error, bytes) => {
      signal.removeEventListener('abort', abort)
      if (signal.aborted) reject(abortError())
      else if (error) reject(error)
      else resolve(bytes)
    })
    function abort() { cancel(); reject(abortError()) }
    signal.addEventListener('abort', abort, { once: true })
  })
}

export function saveArchiveInBrowser(bytes: Uint8Array, filename: string): void {
  const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: 'application/zip' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  try { anchor.click() }
  finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
}

/** Sequential fetching bounds network pressure; ZIP success means handed to the browser, not disk confirmation. */
export async function downloadImages(options: DownloadOptions): Promise<DownloadReport> {
  const { items, mode, signal, onProgress } = options
  const progress: DownloadProgress = { phase: 'fetching', total: items.length, processed: 0, prepared: 0, saved: 0, failed: 0, bytes: 0, current: '' }
  const report: DownloadReport = { cancelled: false, saved: 0, prepared: 0, total: items.length, failures: [], destination: null, records: [] }
  const files: Zippable = {}
  const name = sessionName()
  const emit = () => onProgress?.({ ...progress })
  let directory: DownloadDirectory | null = null
  emit()
  try {
    checkAbort(signal)
    if (mode === 'folder') {
      if (!options.directory) throw new Error('请先选择保存目录')
      directory = await createSessionDirectory(options.directory, name)
      report.destination = `${options.directory.name} / ${directory.name}`
    }
    for (const [index, item] of items.entries()) {
      checkAbort(signal)
      progress.current = item.title
      emit()
      try {
        const image = await fetchImage(item, signal, MAX_DOWNLOAD_BYTES - progress.bytes, options.apiBase)
        checkAbort(signal)
        const filename = `${String(index + 1).padStart(4, '0')}-${safeName(item.title)}.${image.extension}`
        if (directory) await writeFile(directory, filename, image.bytes, signal)
        else files[filename] = image.bytes
        report.records.push({ id: item.id, title: item.title, filename, source: image.source,
          originalUrl: safeHttpUrl(item.imageUrl), thumbnailUrl: safeHttpUrl(item.thumbnailUrl),
          pageUrl: safeHttpUrl(item.pageUrl), similarity: item.similarity, bytes: image.bytes.byteLength })
        progress.bytes += image.bytes.byteLength
        progress.prepared++
        if (directory) progress.saved++
      } catch (error) {
        checkAbort(signal)
        report.failures.push({ item, reason: errorMessage(error) })
        progress.failed++
      }
      progress.processed++
      emit()
    }
    checkAbort(signal)
    const metadata = strToU8(JSON.stringify({ createdAt: new Date().toISOString(), results: report.records,
      failures: report.failures.map(({ item, reason }) => ({ id: item.id, title: item.title, reason,
        imageUrl: safeHttpUrl(item.imageUrl), thumbnailUrl: safeHttpUrl(item.thumbnailUrl), pageUrl: safeHttpUrl(item.pageUrl) })) }, null, 2))
    if (directory) await writeFile(directory, 'results.json', metadata, signal)
    else if (progress.prepared) {
      progress.phase = 'packing'
      progress.current = ''
      emit()
      files['results.json'] = metadata
      const archive = await makeArchive(files, signal)
      checkAbort(signal)
      const filename = `${name}.zip`
      ;(options.saveArchive ?? saveArchiveInBrowser)(archive, filename)
      progress.saved = progress.prepared
      report.destination = filename
    }
  } catch (error) {
    if (signal.aborted) report.cancelled = true
    else report.error = errorMessage(error)
  }
  report.saved = progress.saved
  report.prepared = progress.prepared
  progress.phase = 'complete'
  emit()
  return report
}
