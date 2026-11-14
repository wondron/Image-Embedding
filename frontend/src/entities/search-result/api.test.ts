import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../shared/api'
import { searchGlobalImages, searchLocalImages } from '.'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const emptySearch = { ok: true, trace_id: 't', source: 'empty', scene: 'general', outputs_heating: false, items: [] }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('searchGlobalImages (现网 Global)', () => {
  it('sends URL requests to the configured server and passes cancellation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, trace_id: 't', result: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    await searchGlobalImages({ url: 'https://example.com/a.png', baseUrl: 'https://api.example/', signal })
    expect(fetchMock).toHaveBeenCalledWith('https://api.example/api/search/global/url', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ img_url: 'https://example.com/a.png' }),
      headers: { 'Content-Type': 'application/json' }, signal,
    }))
  })

  it('uploads the file as multipart without manually setting its boundary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, trace_id: 't', result: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['image-data'], 'photo.png', { type: 'image/png' })
    await searchGlobalImages({ file })
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/search/global/file')
    expect(options.body).toBeInstanceOf(FormData)
    expect(options.body.get('file').name).toBe('photo.png')
    expect(options.headers).toBeUndefined()
  })

  it('reports HTTP failures, network failures and invalid responses, preserving abort errors', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ detail: [{ loc: ['body', 'img_url'], msg: 'Field required' }] }, 422))
    vi.stubGlobal('fetch', fetchMock)
    await expect(searchGlobalImages({ url: 'https://example.com/a.png' })).rejects.toThrow('HTTP 422')
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(searchGlobalImages({ url: 'https://example.com/a.png' })).rejects.toThrow('无法连接搜索服务')
    fetchMock.mockResolvedValueOnce(new Response('<html>proxy fallback</html>', { status: 200 }))
    await expect(searchGlobalImages({ url: 'https://example.com/a.png' })).rejects.toThrow('搜索接口未返回有效的 JSON')
    const abort = new DOMException('Aborted', 'AbortError')
    fetchMock.mockRejectedValueOnce(abort)
    await expect(searchGlobalImages({ url: 'https://example.com/a.png' })).rejects.toBe(abort)
  })

  it('sends search_num in the JSON body for links and in the query for files', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json({ ok: true, trace_id: 't', result: [] }))
    vi.stubGlobal('fetch', fetchMock)
    await searchGlobalImages({ url: 'https://example.com/a.png', searchNum: 7 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/search/global/url')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ img_url: 'https://example.com/a.png', search_num: 7 })
    // The file route reads search_num from the query only
    await searchGlobalImages({ file: new File(['image-data'], 'photo.png', { type: 'image/png' }), baseUrl: 'https://api.example', searchNum: 20 })
    expect(fetchMock.mock.calls[1][0]).toBe('https://api.example/api/search/global/file?search_num=20')
    expect([...(fetchMock.mock.calls[1][1].body as FormData).keys()]).toEqual(['file'])
  })

  it('rejects a search_num outside 1–20 or not whole without making requests', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    for (const searchNum of [0, 21, 2.5, Number.NaN]) {
      await expect(searchGlobalImages({ url: 'https://example.com/a.png', searchNum })).rejects.toThrow('返回数量需为 1–20 的整数')
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects invalid inputs without making requests', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await expect(searchGlobalImages({})).rejects.toThrow('请选择')
    await expect(searchGlobalImages({ url: 'javascript:alert(1)' })).rejects.toThrow('HTTP')
    await expect(searchGlobalImages({ file: new File([], 'empty.png') })).rejects.toThrow('为空')
    await expect(searchGlobalImages({ file: new File(['x'], 'a.png'), url: 'https://example.com/a.png' })).rejects.toThrow('请选择')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('searchLocalImages (自建 POST /api/search)', () => {
  it('posts a SearchJsonRequest for links, with the scene mapped off legacy codes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(emptySearch))
    vi.stubGlobal('fetch', fetchMock)
    await searchLocalImages({ url: 'https://example.com/q.png', scene: 'device', scoreThreshold: 0.6, topK: 5, traceId: 'trace-q' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/search')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(init.body)).toEqual({ img_url: 'https://example.com/q.png', scene: 'oven', top_k: 5, score_threshold: 0.6, trace_id: 'trace-q' })
  })

  it('omits a null threshold so the server default applies, and keeps 0 (reject off)', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json(emptySearch))
    vi.stubGlobal('fetch', fetchMock)
    await searchLocalImages({ url: 'https://example.com/q.png', scoreThreshold: null })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ img_url: 'https://example.com/q.png', scene: 'general' })
    await searchLocalImages({ url: 'https://example.com/q.png', scoreThreshold: 0 })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ score_threshold: 0 })
  })

  it('sends multipart files with the same field names', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(emptySearch))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['image-data'], 'q.png', { type: 'image/png' })
    await searchLocalImages({ file, scene: 'oven', scoreThreshold: 0.55, topK: 10 })
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect(form.get('file')).toBeInstanceOf(File)
    expect(Object.fromEntries([...form.entries()].filter(([key]) => key !== 'file')))
      .toEqual({ top_k: '10', score_threshold: '0.55', scene: 'oven' })
    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined()
  })

  it('maps the §6.1 response, including the heating group object', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      ok: true, trace_id: 't', source: 'self', scene: 'oven', outputs_heating: true,
      items: [{ tags: '番茄', description: '', original_url: 'https://s3.example/a.jpg', similarity: 0.8, details: {
        id: 'a', category_id: 1, category: '番茄', scenes: ['oven'], label_status: 'auto',
        images: [{ image_url: 'https://s3.example/a.jpg', thumbnail_url: 'https://s3.example/a.webp', width: 10, height: 10 }],
        food_state: 'cooked', heating_group: { id: 2, code: 'bake', name: '烘焙' },
      } }],
    })))
    const response = await searchLocalImages({ url: 'https://example.com/q.png', scene: 'oven' })
    expect(response.items[0]).toMatchObject({
      id: 'a', thumbnailUrl: 'https://s3.example/a.webp', heatingGroup: { id: 2, code: 'bake', name: '烘焙' }, foodState: 'cooked', source: 'self',
    })
  })

  it('surfaces the error envelope as ApiError and names the self-built service on network failure', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ ok: false, error: 'SEARCH_BAD_IMAGE', detail: 'unsupported image format', trace_id: 'trace-e' }, 422))
    vi.stubGlobal('fetch', fetchMock)
    const error = await searchLocalImages({ url: 'https://example.com/q.png' }).catch(cause => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ message: '搜索失败（HTTP 422 · SEARCH_BAD_IMAGE）：unsupported image format', code: 'SEARCH_BAD_IMAGE', traceId: 'trace-e', status: 422 })
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(searchLocalImages({ url: 'https://example.com/q.png' })).rejects.toThrow('无法连接自建检索服务')
  })
})
