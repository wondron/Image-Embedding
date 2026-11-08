import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildListImagesQuery, indexImage, listImages } from '.'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const png = (name = 'a.png') => new File(['image-data'], name, { type: 'image/png' })
const indexed = (id: string) => ({
  id, filename: `${id}.png`, original_url: `https://s3.example/o/${id}.png`, thumb_url: `https://s3.example/t/${id}.webp`,
  category_id: null, scenes: ['general'], label_status: 'unlabeled', food_state: null, description: '',
  width: 1, height: 1, payload_version: 1, created_at: null,
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('listImages (GET /api/images)', () => {
  it('builds the query: default scene, trimmed q, no 「全部类别」, legacy scene mapped', () => {
    expect(buildListImagesQuery({}).toString()).toBe('page=1&page_size=24&scene=general')
    expect(buildListImagesQuery({ page: 0, pageSize: -1, q: '  番茄 ', category: '全部类别', scene: 'device' }).toString())
      .toBe('page=1&page_size=24&scene=oven&q=%E7%95%AA%E8%8C%84')
    expect(buildListImagesQuery({ page: 3.7, category: ' 蔬菜 ', traceId: ' t1 ' }).toString())
      .toBe('page=3&page_size=24&scene=general&category=%E8%94%AC%E8%8F%9C&trace_id=t1')
  })

  it('requests the page and maps the body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, trace_id: 't', scene: 'oven', page: 2, page_size: 24, total: 30, items: [{ id: 'a' }] }))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    const response = await listImages({ page: 2, scene: 'oven', baseUrl: 'http://127.0.0.1:8320/', signal })
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8320/api/images?page=2&page_size=24&scene=oven', { method: 'GET', signal })
    expect(response).toMatchObject({ scene: 'oven', page: 2, total: 30, items: [{ id: 'a' }] })
  })

  it('reports backend and connection failures with the library wording', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ ok: false, error: 'LIST_UNKNOWN_SCENE', detail: 'unknown scene: x', trace_id: 't' }, 422))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listImages({ scene: 'x' })).rejects.toThrow('图库列表失败（HTTP 422 · LIST_UNKNOWN_SCENE）：unknown scene: x')
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(listImages()).rejects.toThrow('无法连接图库服务')
  })
})

describe('indexImage (POST /api/index)', () => {
  it('sends one file as `file` with category, scenes and description', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, trace_id: 't', indexed: [indexed('a')], failed: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await indexImage({ file: png(), category: '蔬菜', scenes: ['general', 'oven'], tags: ['红'], description: '整颗番茄' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/index')
    const form = init.body as FormData
    expect(form.get('file')).toBeInstanceOf(File)
    expect(form.getAll('files')).toEqual([])
    expect(form.get('category')).toBe('蔬菜')
    expect(form.get('scenes')).toBe('["general","oven"]')
    expect(form.get('tags')).toBe('["红"]')
    expect(form.get('description')).toBe('整颗番茄')
    expect(result.indexed[0]).toMatchObject({ id: 'a', imageUrl: 'https://s3.example/t/a.webp', labelStatus: 'unlabeled' })
  })

  it('sends several files as `files` and leaves out 未分类 and blank descriptions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, trace_id: 't', indexed: [indexed('a'), indexed('b')], failed: [] }))
    vi.stubGlobal('fetch', fetchMock)
    await indexImage({ files: [png('a.png'), png('b.png')], category: '未分类', description: '   ' })
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect(form.getAll('files')).toHaveLength(2)
    expect(form.get('file')).toBeNull()
    expect(form.get('category')).toBeNull()
    expect(form.get('description')).toBeNull()
  })

  it('throws the first per-file failure when nothing was stored', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ ok: true, trace_id: 't', indexed: [], failed: [{ filename: 'a.png', error: 'INDEX_BAD_IMAGE', detail: 'cannot decode image' }] }))
      .mockResolvedValueOnce(json({ ok: true, trace_id: 't', indexed: [], failed: [] }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(indexImage({ file: png() })).rejects.toThrow('入库失败（INDEX_BAD_IMAGE）：cannot decode image')
    await expect(indexImage({ file: png() })).rejects.toThrow('入库接口未返回已索引条目')
  })

  it('validates the batch before any request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await expect(indexImage({})).rejects.toThrow('请选择至少一张')
    await expect(indexImage({ files: Array.from({ length: 101 }, (_, i) => png(`${i}.png`)) })).rejects.toThrow('最多入库 100 张')
    await expect(indexImage({ file: new File([], 'empty.png') })).rejects.toThrow('empty.png：图片文件为空')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
