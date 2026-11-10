import { describe, expect, it } from 'vitest'
import { toIndexResponse, toLibraryImage, toListImagesResponse } from '.'

/** One item as backend ListImagesUseCase emits it for a heating scene (schemas/retrieval.py ListImageItem). */
const ovenItem = {
  id: '0b6f2c1e-0000-4000-8000-000000000002',
  original_url: 'https://s3.example/original/2.jpg',
  thumb_url: 'https://s3.example/thumb/2.webp',
  category_id: 7,
  category: '牛排',
  scenes: ['general', 'oven'],
  label_status: 'auto',
  description: '',
  width: 1024,
  height: 768,
  created_at: '2026-11-07T10:00:00+00:00',
  updated_at: '2026-11-07T11:00:00+00:00',
  heating_group: { id: 5, code: 'meat_grill', name: '肉类烧烤' },
  food_state: 'frozen',
}

describe('toLibraryImage (§6.1.2 / §6.1.4 List map)', () => {
  it('maps every contract field: thumb_url → imageUrl, original_url → originalUrl, heating group kept whole', () => {
    expect(toLibraryImage(ovenItem)).toEqual({
      id: ovenItem.id,
      imageUrl: 'https://s3.example/thumb/2.webp',
      originalUrl: 'https://s3.example/original/2.jpg',
      categoryId: 7,
      category: '牛排',
      scenes: ['general', 'oven'],
      sceneMemberships: [{ code: 'general' }, { code: 'oven' }],
      labelStatus: 'auto',
      description: '',
      foodState: 'frozen',
      heatingGroup: { id: 5, code: 'meat_grill', name: '肉类烧烤' },
      width: 1024,
      height: 768,
      createdAt: '2026-11-07T10:00:00+00:00',
      updatedAt: '2026-11-07T11:00:00+00:00',
    })
  })

  it('reads absent heating keys (non-heating scene) and heating_group: null as null', () => {
    const { heating_group: _h, food_state: _f, ...general } = ovenItem
    expect(toLibraryImage(general)).toMatchObject({ heatingGroup: null, foodState: null })
    expect(toLibraryImage({ ...ovenItem, heating_group: null })?.heatingGroup).toBeNull()
  })

  it('keeps a food_state value added to the enum at runtime', () => {
    expect(toLibraryImage({ ...ovenItem, food_state: 'thawed' })?.foodState).toBe('thawed')
  })

  it('never falls back to forbidden aliases (P2 / P11) and drops rows without an id', () => {
    const item = toLibraryImage({ id: 'x', image_url: 'https://legacy.example/a.png', thumbnail_url: 'https://legacy.example/t.png', qdrant_point_id: 'q', title: '旧标题', tags: ['a'] })
    expect(item).toMatchObject({ imageUrl: null, originalUrl: null, description: '', scenes: [], sceneMemberships: [] })
    expect(item).not.toHaveProperty('title')
    expect(item).not.toHaveProperty('tags')
    expect(toLibraryImage({ thumb_url: 'https://s3.example/t.webp' })).toBeNull()
    expect(toLibraryImage('bad')).toBeNull()
  })

  it('normalizes legacy scene codes once and drops duplicates', () => {
    expect(toLibraryImage({ id: 'x', scenes: ['normal', 'general', ' OVEN ', 'device'] })?.scenes).toEqual(['general', 'oven'])
  })
})

describe('toListImagesResponse', () => {
  it('maps the envelope and filters unusable rows', () => {
    const response = toListImagesResponse({
      ok: true, trace_id: 'trace-l', scene: 'oven', page: 2, page_size: 24, total: 25, items: [ovenItem, null, { thumb_url: 'x' }],
    })
    expect(response).toMatchObject({ traceId: 'trace-l', scene: 'oven', page: 2, pageSize: 24, total: 25 })
    expect(response.items.map(item => item.id)).toEqual([ovenItem.id])
  })

  it('falls back on invalid paging numbers and maps legacy scenes', () => {
    expect(toListImagesResponse({ scene: 'normal', page: 0, page_size: '24', total: -3, items: [] }))
      .toEqual({ traceId: null, scene: 'general', page: 1, pageSize: 24, total: 0, items: [] })
  })

  it('rejects a body without an items array rather than showing an empty library', () => {
    for (const payload of [null, {}, { items: null }, { result: [] }]) {
      expect(() => toListImagesResponse(payload)).toThrow('图库接口返回的数据格式不正确')
    }
  })
})

describe('toIndexResponse (POST /api/index)', () => {
  it('maps IndexedImage and IndexFailure one-to-one', () => {
    expect(toIndexResponse({
      ok: true, trace_id: 'trace-i',
      indexed: [{
        id: 'img-1', filename: 'a.jpg', original_url: 'https://s3.example/o/a.jpg', thumb_url: 'https://s3.example/t/a.webp',
        category_id: null, scenes: ['general'], label_status: 'unlabeled', food_state: null, description: '描述',
        width: 640, height: 480, payload_version: 1, created_at: '2026-11-07T10:00:00+00:00',
      }, { filename: 'no-id.jpg' }],
      failed: [{ filename: 'b.png', error: 'INDEX_BAD_IMAGE', detail: 'cannot decode image' }],
    })).toEqual({
      traceId: 'trace-i',
      indexed: [{
        id: 'img-1', filename: 'a.jpg', imageUrl: 'https://s3.example/t/a.webp', originalUrl: 'https://s3.example/o/a.jpg',
        categoryId: null, scenes: ['general'], labelStatus: 'unlabeled', foodState: null, description: '描述',
        width: 640, height: 480, payloadVersion: 1, createdAt: '2026-11-07T10:00:00+00:00',
      }],
      failed: [{ filename: 'b.png', error: 'INDEX_BAD_IMAGE', detail: 'cannot decode image' }],
    })
  })

  it('reads missing lists as empty', () => {
    expect(toIndexResponse({ ok: true })).toEqual({ traceId: null, indexed: [], failed: [] })
    expect(toIndexResponse(null)).toEqual({ traceId: null, indexed: [], failed: [] })
  })
})
