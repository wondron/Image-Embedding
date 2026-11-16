import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearIndexTasks, deleteIndexTask, listIndexTasks, newImageCount } from '.'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const IMG = '0b6f2c1e-0000-4000-8000-000000000001'
const record = (id: number, fields: Record<string, unknown> = {}) => ({
  id, filename: `${id}.jpg`, status: 'succeeded', image_id: IMG, new_image: true, error_code: null, error_detail: null,
  duration_ms: 10, trace_id: 't', created_at: '2026-11-12T10:00:00+00:00', image_state: 'present',
  image: { id: IMG, thumb_url: 'https://cdn.example/t.webp', category_id: null, category: null, label_status: 'auto' },
  ...fields,
})
const page = (extra: Record<string, unknown> = {}) => ({
  ok: true, status: null, image_state: null, page: 1, page_size: 20, total: 2,
  counts: { succeeded: 2, failed: 0, image_deleted: 1 },
  items: [record(2), record(1, { image_state: 'deleted', image: null })],
  ...extra,
})

afterEach(() => vi.unstubAllGlobals())

describe('listIndexTasks', () => {
  it('reads which records lost their image, per record and in the counts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page())))
    const result = await listIndexTasks()
    // No `repeated` from this (older) server: no repeat is told apart
    expect(result.counts).toEqual({ succeeded: 2, failed: 0, imageDeleted: 1, repeated: 0 })
    expect(result.items.map(item => [item.id, item.imageState, item.image?.id ?? null])).toEqual([[2, 'present', IMG], [1, 'deleted', null]])
    expect(result.imageState).toBeNull()
    expect(result.newImage).toBeNull()
  })

  it('counts one image once when it was uploaded twice: the repeat is not a new image', async () => {
    // 47 files, one of them sent twice (the first request abandoned by the browser, finished by the server)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page({
      total: 65, counts: { succeeded: 48, failed: 17, image_deleted: 0, repeated: 1 },
      items: [record(186, { new_image: false }), record(185)],
    }))))
    const result = await listIndexTasks()
    expect(result.counts).toEqual({ succeeded: 48, failed: 17, imageDeleted: 0, repeated: 1 })
    expect(newImageCount(result.counts)).toBe(47)
    expect(result.items.map(item => [item.id, item.newImage])).toEqual([[186, false], [185, true]])
  })

  it('sends the new-image filter and requires the server to echo it', async () => {
    const repeats = page({ status: 'succeeded', image_state: 'present', new_image: false, total: 1, counts: { succeeded: 2, failed: 0, image_deleted: 0, repeated: 1 }, items: [record(2, { new_image: false })] })
    const fetchMock = vi.fn().mockResolvedValue(json(repeats))
    vi.stubGlobal('fetch', fetchMock)
    const result = await listIndexTasks({ status: 'succeeded', imageState: 'present', newImage: false })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/index-tasks?page=1&page_size=20&status=succeeded&image_state=present&new_image=false')
    expect(result.newImage).toBe(false)
    fetchMock.mockResolvedValueOnce(json({ ...repeats, new_image: undefined }))  // an older server ignores the filter
    await expect(listIndexTasks({ status: 'succeeded', imageState: 'present', newImage: false })).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
    fetchMock.mockResolvedValueOnce(json({ ...repeats, new_image: true }))
    await expect(listIndexTasks({ status: 'succeeded', imageState: 'present', newImage: false })).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
  })

  it('sends the image-state filter and requires the server to echo it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(page({ status: 'succeeded', image_state: 'deleted', total: 1, items: [record(1, { image_state: 'deleted', image: null })] })))
    vi.stubGlobal('fetch', fetchMock)
    await listIndexTasks({ status: 'succeeded', imageState: 'deleted' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/index-tasks?page=1&page_size=20&status=succeeded&image_state=deleted')
    fetchMock.mockResolvedValueOnce(json(page({ status: 'succeeded', image_state: null })))  // filter ignored by the server
    await expect(listIndexTasks({ status: 'succeeded', imageState: 'deleted' })).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
  })

  it('reads a failed record as having no image state, and rejects impossible counts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(page({ counts: { succeeded: 1, failed: 1, image_deleted: 0 }, items: [record(3, { status: 'failed', image_id: null, image_state: 'deleted', image: null })], total: 1 })))
    vi.stubGlobal('fetch', fetchMock)
    expect((await listIndexTasks()).items[0].imageState).toBeNull()
    fetchMock.mockResolvedValueOnce(json(page({ counts: { succeeded: 1, failed: 0, image_deleted: 2 } })))
    await expect(listIndexTasks()).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
    // deleted-image records and repeats are both part of the succeeded ones and never overlap
    fetchMock.mockResolvedValueOnce(json(page({ counts: { succeeded: 2, failed: 0, image_deleted: 1, repeated: 2 } })))
    await expect(listIndexTasks()).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
    fetchMock.mockResolvedValueOnce(json(page({ counts: { succeeded: 2, failed: 0, image_deleted: 1, repeated: -1 } })))
    await expect(listIndexTasks()).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
  })
})

describe('clearIndexTasks', () => {
  it('needs a filter: without one nothing is sent', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await expect(clearIndexTasks({})).rejects.toThrow('需要指定范围')
    await expect(clearIndexTasks({ status: null, imageState: null })).rejects.toThrow('需要指定范围')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('clears the records of deleted images, or the failures, and says how many went', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, status: null, image_state: 'deleted', deleted: 4 }))
      .mockResolvedValueOnce(json({ ok: true, status: 'failed', image_state: null, deleted: 2 }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await clearIndexTasks({ imageState: 'deleted' }, { baseUrl: 'https://api.example' })).toBe(4)
    expect(await clearIndexTasks({ status: 'failed' })).toBe(2)
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
      ['https://api.example/api/index-tasks?image_state=deleted', 'DELETE'],
      ['/api/index-tasks?status=failed', 'DELETE'],
    ])
  })
})

describe('deleteIndexTask', () => {
  it('deletes one record by its id, and refuses ids that cannot be one', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ ok: true, id: 7, deleted: 1 })).mockResolvedValueOnce(json({ ok: true, id: 7, deleted: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await deleteIndexTask(7)).toBe(1)
    expect(await deleteIndexTask(7)).toBe(0)  // already gone
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method])).toEqual([['/api/index-tasks/7', 'DELETE'], ['/api/index-tasks/7', 'DELETE']])
    for (const id of [0, -1, 1.5, Number.NaN]) await expect(deleteIndexTask(id)).rejects.toThrow('编号不正确')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('rejects an answer about another record', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ok: true, id: 8, deleted: 1 })))
    await expect(deleteIndexTask(7)).rejects.toMatchObject({ code: 'TASK_PROTOCOL_ERROR' })
  })
})
