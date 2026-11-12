import { describe, expect, it, vi } from 'vitest'
import { fromPredictResponse, getImageSources, getThumbnailSources } from '.'

/** Bodies as POST /api/predict/searchImage_* returns them: { ok, trace_id, result } (FlyB result cards). */
const predict = (result: unknown, traceId: string | null = 'trace-1') => ({ ok: true, trace_id: traceId, result })
/** A FlyB result card (schemas/predict.py PredictHit); `details` merges over an empty one. */
const card = (fields: Record<string, unknown> = {}, details: Record<string, unknown> = {}) => ({
  tags: '', description: '', original_url: null, similarity: null,
  ...fields,
  details: { rank: 1, page_url: null, site: null, images: [], document_info: null, ...details },
})

describe('fromPredictResponse (现网 Global)', () => {
  it('reads the card fields like a self-built hit and the FlyB details for source and thumbnails', () => {
    const response = fromPredictResponse(predict([card(
      { tags: '西红柿', description: '新鲜西红柿 当季上市', original_url: 'https://img.example/a.jpg', similarity: 0.82 },
      {
        page_url: 'https://source.example/food', site: { Name: '食材百科' },
        images: [{ image_url: 'https://img.example/a.jpg', thumbnail_url: { large: 'https://img.example/large.jpg' }, alt: '番茄图', width: 600, height: 400 }],
      },
    )]))
    expect(response.traceId).toBe('trace-1')
    expect(response.source).toBeUndefined()
    expect(response.items[0]).toEqual({
      id: 'image-1', title: '西红柿', tags: '西红柿', description: '新鲜西红柿 当季上市',
      imageUrl: 'https://img.example/a.jpg', thumbnailUrl: 'https://img.example/large.jpg', alternateUrls: ['https://img.example/large.jpg'],
      similarity: 0.82, categoryId: null, category: null, scenes: [], sceneLabel: null, labelStatus: null,
      foodState: null, heatingGroup: null, width: 600, height: 400,
      pageUrl: 'https://source.example/food', siteName: '食材百科',
    })
  })

  it('reads FlyB as it sends today: capitalized size names (Large first, Tiny last) and HostInfo.Hostname', () => {
    const size = (px: number) => `https://p11-volcsearch.example/pumpkin~${px}x${px}.jpeg`
    const item = fromPredictResponse(predict([card(
      { tags: '永佳 青贝贝南瓜 400g 新鲜蔬菜', original_url: 'https://p3-search.example/pumpkin' },
      {
        page_url: 'http://www.fyuanai.com/s/goods.php?id=486793', site: { Hostname: '合丰', IconUrl: '' },
        images: [{ image_url: 'https://p3-search.example/pumpkin', alt: '', width: 800, height: 800,
          thumbnail_url: { Tiny: size(112), Small: size(224), Medium: size(448), Large: size(896) } }],
      },
    )])).items[0]
    expect(item.thumbnailUrl).toBe(size(896))
    expect(getThumbnailSources(item)).toEqual([size(896), size(448), size(224), size(112), 'https://p3-search.example/pumpkin'])
    expect(item.siteName).toBe('合丰')
  })

  it('falls back to the first safe image original when original_url is not a safe link', () => {
    const item = fromPredictResponse(predict([card({ original_url: 'javascript:alert(1)' }, { images: [
      { image_url: 'javascript:alert(1)' }, { image_url: 'https://img.example/b.jpg', width: 300, height: 200 },
    ] })])).items[0]
    expect(item).toMatchObject({ imageUrl: 'https://img.example/b.jpg', width: 300, height: 200 })
  })

  it('names a result by its tags, then the image alt, and the site by details.site or the page host', () => {
    const items = fromPredictResponse(predict([
      card({ tags: '  ' }, { images: [{ alt: '鲈鱼' }], site: '下厨房' }),
      card({}, { page_url: 'https://www.example.org/recipe' }),
    ])).items
    expect(items.map(item => item.title)).toEqual(['鲈鱼', '未命名图片 2'])
    expect(items.map(item => item.siteName)).toEqual(['下厨房', 'www.example.org'])
  })

  it('does not invent scores or infer categories from food titles', () => {
    const items = fromPredictResponse(predict([
      card({ tags: '蔬菜西红柿', similarity: null }),
      card({ similarity: '0.8' }), card({ similarity: 0 }), card({ similarity: Number.NaN }),
      card({ similarity: Number.POSITIVE_INFINITY }), card({ similarity: 80 }), { ...card(), similarity: undefined, similiar: 0.7 },
    ])).items
    expect(items.map(item => item.similarity)).toEqual([null, null, 0, null, null, null, null])
    expect(items.every(item => item.category === null)).toBe(true)
    expect(items[0].imageUrl).toBeNull()
  })

  it('supports empty lists and upstream empty envelopes with distinct messaging', () => {
    expect(fromPredictResponse(predict([], null)).items).toEqual([])
    const response = fromPredictResponse({
      ok: true,
      result: { request_id: 'upstream-id', total_doc_count: 0, result_count: 0, results: [], status: 'upstream_empty_response', upstream_error: { code: 500, message: 'Empty response' } },
    })
    expect(response.items).toEqual([])
    expect(response.message).toContain('上游搜索服务')
    expect(response.traceId).toBe('upstream-id')
    expect(fromPredictResponse(predict({ results: [], upstream_error: { message: '上游超时' } })).message).toBe('上游超时')
  })

  it('discards malformed records and rejects malformed envelopes', () => {
    const response = fromPredictResponse(predict([null, 'bad', [], 1, { details: null }]))
    expect(response.items).toHaveLength(1)
    expect(response.items[0]).toMatchObject({ title: '未命名图片 1', tags: '', description: '', width: null, height: null })
    for (const payload of [null, {}, { result: null }, { result: 'bad' }, { result: { results: {} } }, { items: [] }]) {
      expect(() => fromPredictResponse(payload)).toThrow('数据格式不正确')
    }
  })

  it('preserves thumbnail-only records and never mutates the original backend record', () => {
    const doc = card({}, { images: [{ thumbnail_url: '//img.example/large.jpg', width: -1, height: '400' }] })
    const before = structuredClone(doc)
    const item = fromPredictResponse(predict([doc])).items[0]
    expect(item.imageUrl).toBeNull()
    expect(item.thumbnailUrl).toBe('https://img.example/large.jpg')
    expect(item.width).toBeNull()
    expect(item.height).toBeNull()
    expect(doc).toEqual(before)
  })

  it('ranks sizes globally, keeps the chosen original first, rejects unsafe variants and deduplicates URLs', () => {
    const item = fromPredictResponse(predict([card({ original_url: 'https://img.example/original.png' }, { images: [
      { image_url: 'https://img.example/original.png', thumbnail_url: { large: 'https://img.example/original-large.png', medium: 'https://img.example/shared.png', tiny: 'https://img.example/tiny.png' } },
      { thumbnail_url: { large: 'javascript:alert(1)', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } },
      { image_url: 'https://img.example/second.png', thumbnail_url: { large: 'https://img.example/detail-large.png', medium: 'https://img.example/shared.png', small: 'file:///secret', other: { invalid: 'nested' } } },
      { thumbnail_url: '//img.example/plain.png' },
    ] })])).items[0]
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
    const doc = card({ original_url: 'https://img.example/not-yet-checked.png' }, { images: [
      { image_url: 'https://img.example/not-yet-checked.png', thumbnail_url: { medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } },
    ] })
    const item = fromPredictResponse(predict([doc])).items[0]
    expect(item.imageUrl).toBe('https://img.example/not-yet-checked.png')
    expect(item.thumbnailUrl).toBe('https://img.example/medium.png')
    expect(getImageSources(item)).toEqual(['https://img.example/not-yet-checked.png', 'https://img.example/medium.png', 'https://img.example/small.png'])
    expect(fetcher).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('retains manually constructed image and thumbnail URLs', () => {
    expect(getImageSources({ imageUrl: 'https://img.example/original.png', thumbnailUrl: 'https://img.example/thumbnail.png' }))
      .toEqual(['https://img.example/original.png', 'https://img.example/thumbnail.png'])
    expect(getImageSources({ imageUrl: null, thumbnailUrl: 'https://img.example/thumbnail.png' }))
      .toEqual(['https://img.example/thumbnail.png'])
  })

  it('lists load the thumbnails first, ranked by size, and the original only after all of them', () => {
    const item = fromPredictResponse(predict([card({ original_url: 'https://img.example/original.png' }, { images: [{
      image_url: 'https://img.example/original.png',
      thumbnail_url: { large: 'https://img.example/large.png', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' },
    }] })])).items[0]
    expect(getThumbnailSources(item)).toEqual([
      'https://img.example/large.png', 'https://img.example/medium.png', 'https://img.example/small.png', 'https://img.example/original.png',
    ])
    expect(getThumbnailSources({ imageUrl: 'https://img.example/original.png', thumbnailUrl: null })).toEqual(['https://img.example/original.png'])
  })
})
