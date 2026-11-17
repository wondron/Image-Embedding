import { afterEach, expect, it, vi } from 'vitest'
import { searchAutoImages, searchLocalBatch } from './api'
import { toBatchSearchResponse } from './batch-search'

afterEach(() => vi.unstubAllGlobals())
const json = (body: unknown) => new Response(JSON.stringify(body))

it('uploads repeated files with shared parameters, preserving threshold 0 and cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue(json({ ok: true, scene: 'oven', results: [] }))
  vi.stubGlobal('fetch', fetch)
  const files = ['a.png', 'b.png'].map(name => new File(['image'], name))
  const signal = new AbortController().signal
  await searchLocalBatch({ files, scene: 'device', scoreThreshold: 0, topK: 4, traceId: 'batch-t', signal })
  const [url, init] = fetch.mock.calls[0]
  expect(url).toBe('/api/search/batch/files')
  expect(init.signal).toBe(signal)
  expect(init.headers).toBeUndefined()
  const form = init.body as FormData
  expect(form.getAll('files').map(file => (file as File).name)).toEqual(['a.png', 'b.png'])
  expect(Object.fromEntries([...form].filter(([key]) => key !== 'files')))
    .toEqual({ scene: 'oven', score_threshold: '0', top_k: '4', trace_id: 'batch-t' })
})

it('rejects empty and oversized batches before sending a request', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  await expect(searchLocalBatch({ files: [] })).rejects.toThrow('至少')
  await expect(searchLocalBatch({ files: Array.from({ length: 31 }, () => new File(['x'], 'a.png')) })).rejects.toThrow('最多')
  expect(fetch).not.toHaveBeenCalled()
})

it('keeps per-image failures, empty successes, timing, and heating flags separate', () => {
  const mapped = toBatchSearchResponse({ ok: true, trace_id: 'batch-t', scene: 'device', outputs_heating: true, results: [
    { index: 0, input: 'bad.png', ok: false, error: 'SEARCH_BAD_IMAGE', detail: 'bad image', elapsed_ms: 12 },
    { index: 1, input: 'empty.png', ok: true, source: 'empty', items: [], elapsed_ms: 250 },
  ] })
  expect(mapped.scene).toBe('oven')
  expect(mapped.outcomes[0]).toMatchObject({ index: 0, response: null, error: 'bad image（SEARCH_BAD_IMAGE）', elapsed: 0.012 })
  expect(mapped.outcomes[1]).toMatchObject({ index: 1, error: null, elapsed: 0.25,
    response: { items: [], source: 'empty', scene: 'oven', outputsHeating: true, traceId: 'batch-t' } })
  expect(() => toBatchSearchResponse({ ok: true })).toThrow('格式')
})

it('business search uses image_url for JSON and file/form fields for uploads', async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(json({ ok: true, trace_id: 't', scene: 'general', items: [], fallback: { status: 'disabled' } })))
  vi.stubGlobal('fetch', fetch)
  await searchAutoImages({ url: 'https://example.test/a.png', scene: 'device', topK: 3, globalFallback: false, scoreThreshold: 0 })
  expect(fetch.mock.calls[0][0]).toBe('/api/search/auto/url')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ image_url: 'https://example.test/a.png', scene: 'oven', top_k: 3, global_fallback: false, score_threshold: 0 })
  await searchAutoImages({ file: new File(['x'], 'a.png'), globalFallback: true, scoreThreshold: null })
  expect(fetch.mock.calls[1][0]).toBe('/api/search/auto/file')
  const form = fetch.mock.calls[1][1].body as FormData
  expect(form.get('file')).toBeInstanceOf(File)
  expect(form.get('global_fallback')).toBe('true')
  expect(form.has('score_threshold')).toBe(false)
})
