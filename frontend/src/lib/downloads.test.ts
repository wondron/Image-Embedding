import { afterEach, describe, expect, it, vi } from 'vitest'
import { strFromU8, unzipSync } from 'fflate'
import { canChooseDownloadDirectory, chooseDownloadDirectory, downloadImages, type DownloadDirectory, type DownloadProgress } from './downloads'
import type { SearchImage } from '../types'

const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
function candidate(id = '1', values: Partial<SearchImage> = {}): SearchImage {
  return { id, title: '同名/:食材', imageUrl: `https://images.example/${id}.jpg`, thumbnailUrl: null,
    similarity: .87, category: null, pageUrl: 'https://source.example/item', siteName: null,
    snippets: [], width: null, height: null, raw: {}, ...values }
}
function imageResponse() { return new Response(png, { headers: { 'Content-Type': 'image/png' } }) }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('downloadImages', () => {
  it('creates one ZIP with unique sanitized actual-format filenames and inspectable metadata', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const saveArchive = vi.fn()
    const progress: DownloadProgress[] = []
    const result = await downloadImages({ items: [candidate('1'), candidate('2')], mode: 'zip',
      signal: new AbortController().signal, saveArchive, onProgress: value => progress.push(value) })
    expect(result).toMatchObject({ saved: 2, prepared: 2, cancelled: false, failures: [] })
    expect(saveArchive).toHaveBeenCalledOnce()
    const files = unzipSync(saveArchive.mock.calls[0][0])
    expect(Object.keys(files)).toEqual(['0001-同名__食材.png', '0002-同名__食材.png', 'results.json'])
    expect(files['0001-同名__食材.png']).toEqual(png)
    const manifest = JSON.parse(strFromU8(files['results.json']))
    expect(manifest.results[0]).toMatchObject({ id: '1', source: 'https://images.example/1.jpg', similarity: .87 })
    expect(progress.find(value => value.phase === 'packing')).toMatchObject({ prepared: 2, saved: 0 })
    expect(progress.at(-1)).toMatchObject({ phase: 'complete', processed: 2, saved: 2 })
  })

  it('falls back to a distinct thumbnail and records the source actually downloaded', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('no', { status: 403 })).mockResolvedValueOnce(imageResponse())
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate('1', { thumbnailUrl: 'https://thumb.example/large.webp' })],
      mode: 'zip', signal: new AbortController().signal, saveArchive: vi.fn() })
    expect(result.saved).toBe(1)
    expect(result.records[0]).toMatchObject({ source: 'https://thumb.example/large.webp', filename: '0001-同名__食材.png' })
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['https://images.example/1.jpg', 'https://thumb.example/large.webp'])
  })

  it('uses medium after original and large fail, preserving the original and actual successful source', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response(null, { status: 404 })).mockResolvedValueOnce(imageResponse())
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate('1', {
      thumbnailUrl: 'https://thumb.example/large.png',
      raw: { thumbnail_url: { large: 'https://thumb.example/large.png', medium: 'https://thumb.example/medium.png', small: 'https://thumb.example/small.png' } },
    })], mode: 'zip', signal: new AbortController().signal, saveArchive: vi.fn() })
    expect(result.saved).toBe(1)
    expect(result.records[0]).toMatchObject({ originalUrl: 'https://images.example/1.jpg', source: 'https://thumb.example/medium.png' })
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      'https://images.example/1.jpg', 'https://thumb.example/large.png', 'https://thumb.example/medium.png',
    ])
  })

  it('uses configured backend proxy for CORS failures without misreporting image source', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(imageResponse())
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate()], apiBase: 'http://localhost:8319/', mode: 'zip',
      signal: new AbortController().signal, saveArchive: vi.fn() })
    expect(result.saved).toBe(1)
    expect(fetcher.mock.calls[1][0]).toBe('http://localhost:8319/api/images/download?url=https%3A%2F%2Fimages.example%2F1.jpg')
    expect(result.records[0].source).toBe('https://images.example/1.jpg')
  })

  it('rejects unsafe URLs and distinctness prevents retrying the same thumbnail', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('no', { status: 404 }))
    vi.stubGlobal('fetch', fetcher)
    const saveArchive = vi.fn()
    const result = await downloadImages({ items: [candidate('1', { thumbnailUrl: 'https://images.example/1.jpg' }),
      candidate('2', { imageUrl: 'javascript:alert(1)', thumbnailUrl: 'file:///secret' })],
      mode: 'zip', signal: new AbortController().signal, saveArchive })
    expect(fetcher).toHaveBeenCalledOnce()
    expect(saveArchive).not.toHaveBeenCalled()
    expect(result).toMatchObject({ saved: 0, failures: [{ reason: 'HTTP 404' }, { reason: '没有有效的图片地址' }] })
  })

  it('includes partial failure records in the ZIP rather than silently calling all results saved', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(imageResponse()).mockRejectedValueOnce(new TypeError('Failed to fetch')))
    const saveArchive = vi.fn()
    const result = await downloadImages({ items: [candidate('1'), candidate('2')], mode: 'zip',
      signal: new AbortController().signal, saveArchive })
    expect(result.saved).toBe(1)
    expect(result.failures).toHaveLength(1)
    const manifest = JSON.parse(strFromU8(unzipSync(saveArchive.mock.calls[0][0])['results.json']))
    expect(manifest.failures).toEqual([expect.objectContaining({ id: '2', imageUrl: 'https://images.example/2.jpg', reason: '源站限制跨域访问或网络不可用' })])
  })

  it('rejects HTML error pages, empty bodies, and oversized streamed image content', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('<html>error</html>', { headers: { 'Content-Type': 'text/html' } }))
      .mockResolvedValueOnce(new Response(new Uint8Array(), { headers: { 'Content-Type': 'image/png' } }))
      .mockResolvedValueOnce(new Response(png, { headers: { 'Content-Type': 'image/png', 'Content-Length': '300000000' } })))
    const result = await downloadImages({ items: [candidate('1'), candidate('2'), candidate('3')], mode: 'zip',
      signal: new AbortController().signal, saveArchive: vi.fn() })
    expect(result.saved).toBe(0)
    expect(result.failures.map(failure => failure.reason)).toEqual(['源站未返回支持的图片格式', '图片内容为空', '超过本次下载 200 MB 容量上限'])
  })

  it('cancels ZIP after partial fetch without saving an incomplete archive', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn(async () => imageResponse())
    vi.stubGlobal('fetch', fetcher)
    const saveArchive = vi.fn()
    const result = await downloadImages({ items: [candidate('1'), candidate('2')], mode: 'zip', signal: controller.signal,
      saveArchive, onProgress: value => { if (value.processed === 1) controller.abort() } })
    expect(result).toMatchObject({ cancelled: true, saved: 0, prepared: 1, failures: [] })
    expect(fetcher).toHaveBeenCalledOnce()
    expect(saveArchive).not.toHaveBeenCalled()
  })

  it('cancels an in-flight request without recording a failure or trying its thumbnail', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      queueMicrotask(() => controller.abort())
    }))
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate('1', { thumbnailUrl: 'https://thumb.example/a.jpg' })],
      mode: 'zip', signal: controller.signal, saveArchive: vi.fn() })
    expect(result).toMatchObject({ cancelled: true, saved: 0, prepared: 0, failures: [] })
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('honors cancellation when ZIP packing is about to begin', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const saveArchive = vi.fn()
    const result = await downloadImages({ items: [candidate()], mode: 'zip', signal: controller.signal, saveArchive,
      onProgress: value => { if (value.phase === 'packing') controller.abort() } })
    expect(result).toMatchObject({ cancelled: true, saved: 0, prepared: 1 })
    expect(saveArchive).not.toHaveBeenCalled()
  })

  it('does not fetch or create an archive when cancelled before starting', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate()], mode: 'zip', signal: controller.signal, saveArchive: vi.fn() })
    expect(result).toMatchObject({ cancelled: true, saved: 0, prepared: 0 })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('reports an archive-save failure with zero saved even when downloads were prepared', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const result = await downloadImages({ items: [candidate()], mode: 'zip', signal: new AbortController().signal,
      saveArchive: () => { throw new Error('浏览器拒绝下载') } })
    expect(result).toMatchObject({ saved: 0, prepared: 1, error: '浏览器拒绝下载' })
  })

  it('still creates a ZIP on an ordinary HTTP LAN context without crypto.randomUUID', async () => {
    vi.stubGlobal('crypto', undefined)
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const saveArchive = vi.fn()
    const result = await downloadImages({ items: [candidate()], mode: 'zip', signal: new AbortController().signal, saveArchive })
    expect(result.saved).toBe(1)
    expect(saveArchive.mock.calls[0][1]).toMatch(/^images-.*\.zip$/)
  })
})

function mockDirectory() {
  const writes: Record<string, Uint8Array> = {}
  const child: DownloadDirectory = {
    name: '', getDirectoryHandle: vi.fn(),
    getFileHandle: vi.fn(async (name: string) => ({ createWritable: async () => ({
      write: async (data: Uint8Array) => { writes[name] = data }, close: async () => {}, abort: async () => {},
    }) })),
  }
  const parent: DownloadDirectory = { name: '选择的目录', getFileHandle: vi.fn(), getDirectoryHandle: vi.fn(async (name, options) => {
    if (!options?.create) throw new DOMException('missing', 'NotFoundError')
    child.name = name
    return child
  }) }
  return { parent, child, writes }
}

describe('directory saving', () => {
  it('creates a fresh child directory and writes image bytes and a source manifest', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const { parent, writes } = mockDirectory()
    const result = await downloadImages({ items: [candidate()], mode: 'folder', directory: parent, signal: new AbortController().signal })
    expect(result.saved).toBe(1)
    expect(result.destination).toMatch(/^选择的目录 \/ images-/)
    expect(writes['0001-同名__食材.png']).toEqual(png)
    expect(JSON.parse(strFromU8(writes['results.json'])).results).toHaveLength(1)
  })

  it('keeps already-written files on cancellation and stops before the next image', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const controller = new AbortController()
    const { parent, writes } = mockDirectory()
    const result = await downloadImages({ items: [candidate('1'), candidate('2')], mode: 'folder', directory: parent,
      signal: controller.signal, onProgress: value => { if (value.saved === 1) controller.abort() } })
    expect(result).toMatchObject({ cancelled: true, saved: 1, prepared: 1 })
    expect(Object.keys(writes)).toEqual(['0001-同名__食材.png'])
  })

  it('does not claim a destination or download when directory permission is denied', async () => {
    const { parent } = mockDirectory()
    parent.getDirectoryHandle = vi.fn(async () => { throw new DOMException('Permission denied', 'NotAllowedError') })
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const result = await downloadImages({ items: [candidate()], mode: 'folder', directory: parent, signal: new AbortController().signal })
    expect(result).toMatchObject({ saved: 0, prepared: 0, destination: null, error: 'Permission denied' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('does not overwrite an existing child directory and continues with a fresh name', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const { parent, child } = mockDirectory()
    parent.getDirectoryHandle = vi.fn()
      .mockResolvedValueOnce(child)
      .mockRejectedValueOnce(new DOMException('missing', 'NotFoundError'))
      .mockImplementationOnce(async (name: string) => { child.name = name; return child })
    const result = await downloadImages({ items: [candidate()], mode: 'folder', directory: parent, signal: new AbortController().signal })
    expect(result.saved).toBe(1)
    expect(result.destination).toMatch(/-1$/)
    const calls = vi.mocked(parent.getDirectoryHandle).mock.calls
    expect(calls[0][1]).toBeUndefined()
    expect(calls[1][1]).toBeUndefined()
    expect(calls[2]).toEqual([expect.stringMatching(/-1$/), { create: true }])
  })

  it('reports folder file-write failure without counting that image as saved', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => imageResponse()))
    const { parent, child } = mockDirectory()
    const abort = vi.fn(async () => {})
    const existing = child.getFileHandle
    child.getFileHandle = vi.fn(async (name, options) => name.endsWith('.png')
      ? { createWritable: async () => ({ write: async () => { throw new Error('磁盘空间不足') }, close: vi.fn(), abort }) }
      : existing(name, options))
    const result = await downloadImages({ items: [candidate()], mode: 'folder', directory: parent, signal: new AbortController().signal })
    expect(result).toMatchObject({ saved: 0, prepared: 0, failures: [{ reason: '磁盘空间不足' }] })
    expect(abort).toHaveBeenCalledOnce()
  })

  it('only enables and invokes the directory picker in a supported secure context', async () => {
    vi.stubGlobal('window', { isSecureContext: false, showDirectoryPicker: vi.fn() })
    expect(canChooseDownloadDirectory()).toBe(false)
    await expect(chooseDownloadDirectory()).rejects.toThrow('请使用 ZIP')
    const { parent } = mockDirectory()
    const picker = vi.fn(async () => parent)
    vi.stubGlobal('window', { isSecureContext: true, showDirectoryPicker: picker })
    expect(canChooseDownloadDirectory()).toBe(true)
    await expect(chooseDownloadDirectory()).resolves.toBe(parent)
    expect(picker).toHaveBeenCalledWith({ mode: 'readwrite' })
  })
})
