import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRoot, readableApiError, requestJson, requestJsonResult } from '.'

const labels = { service: '图库服务', endpoint: '图库接口', action: '图库列表' }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('requestJson', () => {
  it('joins origin, path and query, and sends JSON with its content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    await expect(requestJson('/api/search', {
      baseUrl: 'https://api.example//', method: 'POST', json: { img_url: 'https://a.example/q.png' },
      query: new URLSearchParams({ scene: 'oven' }), signal, labels,
    })).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith('https://api.example/api/search?scene=oven', {
      method: 'POST', signal, body: '{"img_url":"https://a.example/q.png"}', headers: { 'Content-Type': 'application/json' },
    })
  })

  it('sends multipart without a manual boundary header, same-origin by default', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const form = new FormData()
    await requestJson('/api/index', { method: 'POST', form, labels })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/index')
    expect(init.body).toBe(form)
    expect(init.headers).toBeUndefined()
  })

  it('turns the backend error envelope into an ApiError with code, status and trace id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: false, error: 'LIST_POSTGRES_FAILED', detail: 'metadata store unavailable', trace_id: 'trace-9',
    }), { status: 502 })))
    const error = await requestJson('/api/images', { labels }).catch(cause => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      message: '图库列表失败（HTTP 502 · LIST_POSTGRES_FAILED）：metadata store unavailable',
      status: 502, code: 'LIST_POSTGRES_FAILED', traceId: 'trace-9',
    })
  })

  it('distinguishes network failures, non-JSON bodies and aborts', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(requestJson('/api/images', { labels })).rejects.toThrow('无法连接图库服务')
    fetchMock.mockResolvedValueOnce(new Response('<html>proxy</html>', { status: 200 }))
    await expect(requestJson('/api/images', { labels })).rejects.toThrow('图库接口未返回有效的 JSON 数据')
    fetchMock.mockResolvedValueOnce(new Response('<html>busy</html>', { status: 503 }))
    await expect(requestJson('/api/images', { labels })).rejects.toThrow('图库列表服务尚未就绪（HTTP 503）')
    const abort = new DOMException('Aborted', 'AbortError')
    fetchMock.mockRejectedValueOnce(abort)
    await expect(requestJson('/api/images', { labels })).rejects.toBe(abort)
  })

  it('reads X-Trace-Id when the success body has no trace_id, and prefers the body on errors', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, enums: [] }), {
        status: 200,
        headers: { 'X-Trace-Id': 'trace-header' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: false, error: 'ENUM_FILE_INVALID', detail: 'broken', trace_id: 'trace-body',
      }), {
        status: 503,
        headers: { 'X-Trace-Id': 'trace-header' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(requestJsonResult('/api/enums', { labels })).resolves.toMatchObject({
      status: 200, traceId: 'trace-header', body: { ok: true, enums: [] },
    })
    const error = await requestJsonResult('/api/enums', { labels }).catch(cause => cause)
    expect(error).toMatchObject({ code: 'ENUM_FILE_INVALID', traceId: 'trace-body', detail: 'broken', status: 503 })
  })

  it('strips trailing slashes from the API origin', () => {
    expect(apiRoot('http://127.0.0.1:8320///')).toBe('http://127.0.0.1:8320')
    expect(apiRoot('')).toBe('')
  })
})

describe('readableApiError', () => {
  it('formats FastAPI validation arrays, details, codes and HTTP fallbacks for humans', () => {
    const fastApi = { detail: [{ loc: ['body', 'img_url'], msg: 'Field required', type: 'missing' }] }
    expect(readableApiError(fastApi, 422)).toContain('body.img_url: Field required')
    expect(readableApiError({ ok: false, error: 'VALIDATION_ERROR', detail: 'img_url: Field required', trace_id: 't' }, 422))
      .toBe('搜索失败（HTTP 422 · VALIDATION_ERROR）：img_url: Field required')
    expect(readableApiError({ detail: 'service unavailable' }, 503)).toContain('service unavailable')
    expect(readableApiError({ error: 'INDEX_BAD_IMAGE' }, 422, '入库')).toBe('入库失败（HTTP 422）：INDEX_BAD_IMAGE')
    expect(readableApiError({}, 504)).toContain('超时')
    expect(readableApiError(null, 413)).toContain('图片过大')
  })
})
