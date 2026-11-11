import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildListImagesQuery, deleteImage, getImage, indexImage, listImages, payloadDifferences, pushImageVector, reembedImage, updateImage } from '.'

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
  it('adds the category subtree and label status filters', () => {
    expect(buildListImagesQuery({ categoryId: 12, labelStatus: 'auto' }).toString())
      .toBe('page=1&page_size=24&scene=general&category_id=12&label_status=auto')
    expect(buildListImagesQuery({ categoryId: 0, labelStatus: null }).toString()).toBe('page=1&page_size=24&scene=general')
  })

  it('asks for the images that need a sync or a vector only when the filter is on', () => {
    expect(buildListImagesQuery({ outOfSync: true }).toString()).toBe('page=1&page_size=24&scene=general&out_of_sync=true')
    expect(buildListImagesQuery({ outOfSync: false }).toString()).toBe('page=1&page_size=24&scene=general')
  })

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

  it('sends the labelling and food fields; label_status only with a category', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json({ ok: true, trace_id: 't', indexed: [indexed('a')], failed: [] })))
    vi.stubGlobal('fetch', fetchMock)
    await indexImage({
      file: png(), category: 'dish.steak', labelStatus: 'verified', sourceType: ' crawled ', license: 'commercial',
      foodState: 'cooked', foodProcessing: null, foodCount: 2, totalMassG: 350, waterMassG: 0, mainIngredientMassG: null,
    })
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect(Object.fromEntries([...form.entries()].filter(([key]) => key !== 'file'))).toEqual({
      category: 'dish.steak', label_status: 'verified', source_type: 'crawled', license: 'commercial',
      food_state: 'cooked', food_count: '2', total_mass_g: '350', water_mass_g: '0',
    })
    await indexImage({ file: png(), labelStatus: 'verified' })  // no category: the server would refuse the label
    expect((fetchMock.mock.calls[1][1].body as FormData).get('label_status')).toBeNull()
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

const IMAGE_WIRE = {
  id: 'img-1', sha256: 'ab'.repeat(32), original_url: 'https://s3.example/o.jpg', thumb_url: 'https://s3.example/t.webp',
  width: 640, height: 480, category_id: 3, category: '牛排', category_code: 'dish.steak', label_status: 'auto',
  scenes: ['general', 'oven'], description: '', tags: ['带骨'], source_type: 'user_upload', license: 'unknown',
  food_state: 'raw', food_processing: null, food_count: 2, total_mass_g: null, water_mass_g: 0, main_ingredient_mass_g: null,
  heating_group: null, payload_version: 4, created_at: null, updated_at: null,
  embeddings: [{ model_id: 7, name: 'dino', version: 'v2', collection: 'c2', model_status: 'current', synced_version: 3, embedded_at: null }],
}

describe('one image (GET / PATCH / DELETE /api/images/{id}, POST vector-sync)', () => {
  it('reads the detail and its vector state', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, image: IMAGE_WIRE, vector: {
      status: 'stale', collection: 'c2', payload: { scenes: ['general'], category_id: 3 }, expected_payload: { scenes: ['oven', 'general'], category_id: 3 },
      payload_version: 4, synced_version: 3,
    } }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await getImage('img/1', { baseUrl: 'http://api' })
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/images/img%2F1')
    expect(result.image).toMatchObject({ id: 'img-1', categoryCode: 'dish.steak', foodCount: 2, waterMassG: 0, tags: ['带骨'] })
    expect(result.image.embeddings).toEqual([{ modelId: 7, name: 'dino', version: 'v2', collection: 'c2', modelStatus: 'current', syncedVersion: 3, embeddedAt: null }])
    expect(result.vector).toMatchObject({ status: 'stale', payloadVersion: 4, syncedVersion: 3 })
    expect(payloadDifferences(result.vector)).toEqual([['scenes', ['general'], ['oven', 'general']]])
  })

  it('sends the patch as JSON and reads how it reached the index', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, image: { ...IMAGE_WIRE, category_id: 4 }, vector_sync: 'missing', sync_required: true }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateImage('img-1', { category_id: 4, label_status: 'verified' })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ category_id: 4, label_status: 'verified' })
    expect(result).toMatchObject({ vectorSync: 'missing', syncRequired: true, image: { categoryId: 4 } })
    expect(result.vector).toBeNull()  // an older server: the page reads the detail once more
  })

  it('reads the vector state a write answers with, so no second request is needed', async () => {
    const vector = { status: 'synced', collection: 'c2', payload_version: 5, synced_version: 5, payload_matches: true }
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(json({ ok: true, image: IMAGE_WIRE, vector, vector_sync: 'pushed', sync_required: false }))
      .mockResolvedValueOnce(json({ ok: true, image_id: 'img-1', payload_version: 5, vector, vector_sync: 'pushed', sync_required: false }))
      .mockResolvedValueOnce(json({ ok: true, image_id: 'img-1', payload_version: 5, vector: { status: 'nonsense' }, vector_sync: 'failed', sync_required: true })))
    expect((await updateImage('img-1', { tags: [] })).vector).toMatchObject({ status: 'synced', payloadVersion: 5, syncedVersion: 5 })
    expect((await pushImageVector('img-1')).vector).toMatchObject({ status: 'synced', collection: 'c2' })
    expect((await pushImageVector('img-1')).vector?.status).toBe('unavailable')  // off-contract reads as unknown
  })

  it('pushes and deletes; a missing image is IMAGE_NOT_FOUND', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, image_id: 'img-1', payload_version: 4, vector_sync: 'pushed', sync_required: false }))
      .mockResolvedValueOnce(json({ ok: true, image_id: 'img-1', deleted_at: '2026-11-11T00:00:00+00:00', cleanup: 'queued' }))
      .mockResolvedValueOnce(json({ ok: false, error: 'IMAGE_NOT_FOUND', detail: 'image img-1 does not exist', trace_id: 't' }, 404))
    vi.stubGlobal('fetch', fetchMock)
    expect(await pushImageVector('img-1')).toMatchObject({ vectorSync: 'pushed', syncRequired: false, payloadVersion: 4 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/images/img-1/vector-sync')
    expect(await deleteImage('img-1')).toMatchObject({ cleanup: 'queued', deletedAt: '2026-11-11T00:00:00+00:00' })
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE')
    await expect(getImage('img-1')).rejects.toMatchObject({ code: 'IMAGE_NOT_FOUND', status: 404 })
  })

  it('re-embeds from the stored original and reads the vector state after it; failures keep their code', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, image_id: 'img-1', payload_version: 4, vector: { status: 'synced', collection: 'c2', synced_version: 4 } }))
      .mockResolvedValueOnce(json({ ok: false, error: 'IMAGE_FILE_UNAVAILABLE', detail: 'stored original could not be read', trace_id: 't' }, 502))
    vi.stubGlobal('fetch', fetchMock)
    const result = await reembedImage('img-1')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/images/img-1/reembed')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(result).toMatchObject({ imageId: 'img-1', payloadVersion: 4, vector: { status: 'synced', syncedVersion: 4 } })
    await expect(reembedImage('img-1')).rejects.toMatchObject({ code: 'IMAGE_FILE_UNAVAILABLE', status: 502 })
  })

  it('treats an unreadable body as unknown, not as success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ok: true, image: { no: 'id' } })))
    await expect(updateImage('img-1', { tags: [] })).rejects.toMatchObject({ code: 'IMAGE_PROTOCOL_ERROR' })
  })
})
