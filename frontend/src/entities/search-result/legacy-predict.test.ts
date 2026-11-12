import { describe, expect, it, vi } from 'vitest'
import { fromPredictResponse, getImageSources, getThumbnailSources } from '.'

/** Bodies as POST /api/predict/searchImage_* returns them: { ok, trace_id, result } (FlyB passthrough). */
const predict = (result: unknown, traceId: string | null = 'trace-1') => ({ ok: true, trace_id: traceId, result })

describe('fromPredictResponse (现网 Global, legacy)', () => {
  it('retains real scores and details while finding valid image and large thumbnail URLs', () => {
    const doc = {
      title: '西红柿', image_url: 'javascript:alert(1)', similarity: 0.82, category: '蔬菜',
      details: {
        page_url: 'https://source.example/food', site: { Name: '食材百科' },
        text_snippets: ['新鲜西红柿', null, 12],
        images: [{ image_url: 'https://img.example/a.jpg', thumbnail_url: { large: 'https://img.example/large.jpg' }, width: 600, height: 400 }],
      },
    }
    const response = fromPredictResponse(predict([doc]))
    expect(response.traceId).toBe('trace-1')
    expect(response.source).toBeUndefined()
    expect(response.items[0]).toMatchObject({
      title: '西红柿', imageUrl: 'https://img.example/a.jpg', thumbnailUrl: 'https://img.example/large.jpg',
      similarity: 0.82, category: '蔬菜', pageUrl: 'https://source.example/food', siteName: '食材百科',
      width: 600, height: 400, snippets: ['新鲜西红柿'],
      heatingGroup: null, foodState: null, description: '', scenes: [],
    })
  })

  it('does not invent scores or infer categories from food titles', () => {
    const items = fromPredictResponse(predict([
      { title: '蔬菜西红柿', similarity: null, similiar: null },
      { similarity: '0.8' }, { similarity: 0 }, { similarity: Number.NaN },
      { similarity: Number.POSITIVE_INFINITY }, { similarity: 80 }, { similiar: 0.7 },
    ])).items
    expect(items.map(item => item.similarity)).toEqual([null, null, 0, null, null, null, 0.7])
    expect(items.every(item => item.category === null)).toBe(true)
    expect(items[0].imageUrl).toBeNull()
  })

  it('supports empty lists and upstream empty envelopes with distinct messaging', () => {
    expect(fromPredictResponse(predict([], null)).items).toEqual([])
    const response = fromPredictResponse({
      ok: true,
      result: { results: [], status: 'upstream_empty_response', request_id: 'upstream-id', upstream_error: { code: 500, message: 'Empty response' } },
    })
    expect(response.items).toEqual([])
    expect(response.message).toContain('上游搜索服务')
    expect(response.traceId).toBe('upstream-id')
    expect(fromPredictResponse(predict({ results: [], upstream_error: { message: '上游超时' } })).message).toBe('上游超时')
  })

  it('discards malformed records and rejects malformed envelopes', () => {
    const response = fromPredictResponse(predict([null, 'bad', [], 1, { details: null }]))
    expect(response.items).toHaveLength(1)
    expect(response.items[0]).toMatchObject({ title: '未命名图片 1', snippets: [], width: null, height: null })
    for (const payload of [null, {}, { result: null }, { result: 'bad' }, { result: { results: {} } }, { items: [] }]) {
      expect(() => fromPredictResponse(payload)).toThrow('数据格式不正确')
    }
  })

  it('preserves thumbnail-only records and never mutates the original backend record', () => {
    const doc = { details: { images: [{ thumbnail_url: '//img.example/large.jpg', width: -1, height: '400' }] } }
    const item = fromPredictResponse(predict([doc])).items[0]
    expect(item.imageUrl).toBeNull()
    expect(item.thumbnailUrl).toBe('https://img.example/large.jpg')
    expect(item.width).toBeNull()
    expect(item.height).toBeNull()
    expect(doc).not.toHaveProperty('id')
  })

  it('preserves an upstream large-thumbnail object in the primary image_url field', () => {
    const item = fromPredictResponse(predict([{ title: '缩略图回退', image_url: { large: 'https://img.example/fallback.webp' } }])).items[0]
    expect(item.imageUrl).toBeNull()
    expect(item.thumbnailUrl).toBe('https://img.example/fallback.webp')
  })

  it('ranks sizes globally, keeps the chosen original first, rejects unsafe variants and deduplicates URLs', () => {
    const item = fromPredictResponse(predict([{
      image_url: { large: 'https://img.example/original-large.png', medium: 'https://img.example/shared.png', tiny: 'https://img.example/tiny.png' },
      thumbnail_url: { large: 'javascript:alert(1)', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' },
      details: { images: [
        { image_url: 'https://img.example/original.png', thumbnail_url: { large: 'https://img.example/detail-large.png', medium: 'https://img.example/shared.png', small: 'file:///secret', other: { invalid: 'nested' } } },
        { thumbnail_url: '//img.example/plain.png' },
      ] },
    }])).items[0]
    expect(getImageSources(item)).toEqual([
      'https://img.example/original.png', 'https://img.example/original-large.png',
      'https://img.example/detail-large.png', 'https://img.example/plain.png',
      'https://img.example/shared.png', 'https://img.example/medium.png',
      'https://img.example/small.png', 'https://img.example/tiny.png',
    ])
  })

  it('preserves unverified original URLs without fetching, and exposes medium-only thumbnails', () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const doc = { image_url: 'https://img.example/not-yet-checked.png', thumbnail_url: { medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } }
    const item = fromPredictResponse(predict([doc])).items[0]
    expect(item.imageUrl).toBe(doc.image_url)
    expect(item.thumbnailUrl).toBe('https://img.example/medium.png')
    expect(getImageSources(item)).toEqual([doc.image_url, 'https://img.example/medium.png', 'https://img.example/small.png'])
    expect(fetcher).not.toHaveBeenCalled()
    expect(doc).toEqual({ image_url: 'https://img.example/not-yet-checked.png', thumbnail_url: { medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } })
    vi.unstubAllGlobals()
  })

  it('retains manually constructed image and thumbnail URLs', () => {
    expect(getImageSources({ imageUrl: 'https://img.example/original.png', thumbnailUrl: 'https://img.example/thumbnail.png' }))
      .toEqual(['https://img.example/original.png', 'https://img.example/thumbnail.png'])
    expect(getImageSources({ imageUrl: null, thumbnailUrl: 'https://img.example/thumbnail.png' }))
      .toEqual(['https://img.example/thumbnail.png'])
  })

  it('lists load the thumbnails first, ranked by size, and the original only after all of them', () => {
    const doc = {
      image_url: 'https://img.example/original.png',
      thumbnail_url: { large: 'https://img.example/large.png', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' },
    }
    const item = fromPredictResponse(predict([doc])).items[0]
    expect(getThumbnailSources(item)).toEqual([
      'https://img.example/large.png', 'https://img.example/medium.png', 'https://img.example/small.png', 'https://img.example/original.png',
    ])
    expect(getThumbnailSources({ imageUrl: 'https://img.example/original.png', thumbnailUrl: null })).toEqual(['https://img.example/original.png'])
  })
})
