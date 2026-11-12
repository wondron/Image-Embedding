import { describe, expect, it } from 'vitest'
import { getImageSources, getThumbnailSources, toSearchResponse } from '.'

/** One hit as backend SearchImageUseCase emits it for a heating scene (schemas/retrieval.py SearchHit). */
const ovenHit = {
  tags: '番茄、带蒂',
  description: '整颗番茄，带蒂',
  original_url: 'https://s3.example/original/1.jpg',
  similarity: 0.91,
  details: {
    id: '0b6f2c1e-0000-4000-8000-000000000001',
    category_id: 12,
    category: '番茄',
    scenes: ['general', 'oven'],
    scene_label: '烤番茄',
    label_status: 'verified',
    images: [{ image_url: 'https://s3.example/original/1.jpg', thumbnail_url: 'https://s3.example/thumb/1.webp', width: 800, height: 600 }],
    food_state: 'raw',
    heating_group: { id: 3, code: 'veg_roast', name: '蔬菜烘烤' },
  },
}
type Details = Record<string, unknown>
const withDetails = (details: Details, hit: Record<string, unknown> = ovenHit) => ({ ...hit, details: { ...ovenHit.details, ...details } })
const envelope = (items: unknown[], extra: Record<string, unknown> = {}) => ({
  ok: true, trace_id: 'trace-1', source: 'self', scene: 'oven', outputs_heating: true, items, ...extra,
})

describe('toSearchResponse (§6.1.1 / §6.1.4)', () => {
  it('maps the card fields and details one-to-one and keeps heating_group as an object', () => {
    const response = toSearchResponse(envelope([ovenHit]))
    expect(response).toEqual({
      items: [{
        id: ovenHit.details.id,
        title: '烤番茄',
        tags: '番茄、带蒂',
        imageUrl: 'https://s3.example/original/1.jpg',
        thumbnailUrl: 'https://s3.example/thumb/1.webp',
        alternateUrls: [],
        similarity: 0.91,
        categoryId: 12,
        category: '番茄',
        scenes: ['general', 'oven'],
        sceneLabel: '烤番茄',
        labelStatus: 'verified',
        description: '整颗番茄，带蒂',
        foodState: 'raw',
        heatingGroup: { id: 3, code: 'veg_roast', name: '蔬菜烘烤' },
        width: 800,
        height: 600,
        outputsHeating: true,
        source: 'self',
        pageUrl: null,
        siteName: null,
      }],
      traceId: 'trace-1',
      source: 'self',
      scene: 'oven',
      outputsHeating: true,
    })
    expect(getImageSources(response.items[0])).toEqual(['https://s3.example/original/1.jpg', 'https://s3.example/thumb/1.webp'])
    // Lists: the 512 px thumbnail first
    expect(getThumbnailSources(response.items[0])).toEqual(['https://s3.example/thumb/1.webp', 'https://s3.example/original/1.jpg'])
  })

  it('reads heating_group: null (category without a group) and absent keys (non-heating scene) as null', () => {
    const noGroup = toSearchResponse(envelope([withDetails({ heating_group: null, food_state: null })])).items[0]
    expect(noGroup).toMatchObject({ heatingGroup: null, foodState: null, outputsHeating: true })
    const { food_state: _f, heating_group: _h, scene_label: _s, ...generalDetails } = ovenHit.details
    const general = toSearchResponse(envelope([{ ...ovenHit, details: generalDetails }], { scene: 'general', outputs_heating: false }))
    expect(general.outputsHeating).toBe(false)
    expect(general.items[0]).toMatchObject({ heatingGroup: null, foodState: null, sceneLabel: null, title: '番茄', outputsHeating: false })
  })

  it('names a hit by scene label, then category, then its tags', () => {
    const title = (details: Details, tags = '红薯叶、带皮') => toSearchResponse(envelope([withDetails(details, { ...ovenHit, tags })])).items[0].title
    expect(title({})).toBe('烤番茄')
    expect(title({ scene_label: null })).toBe('番茄')
    expect(title({ scene_label: null, category: null })).toBe('红薯叶、带皮')
    expect(title({ scene_label: null, category: null }, '  ')).toBe('未命名图片 1')
  })

  it('rejects malformed heating groups and label statuses instead of guessing', () => {
    for (const heating_group of ['蔬菜烘烤', { id: 3, code: 'veg_roast' }, { id: '3', code: 'veg_roast', name: '蔬菜烘烤' }, { id: 3.5, code: 'x', name: 'y' }, []]) {
      expect(toSearchResponse(envelope([withDetails({ heating_group })])).items[0].heatingGroup).toBeNull()
    }
    expect(toSearchResponse(envelope([withDetails({ label_status: 'maybe' })])).items[0].labelStatus).toBeNull()
  })

  it('keeps any food_state text: an editable enum gains values at runtime (GET /api/enums/food_state)', () => {
    const foodState = (food_state: unknown) => toSearchResponse(envelope([withDetails({ food_state })])).items[0].foodState
    expect(foodState('thawed')).toBe('thawed')
    expect(foodState(' thawed ')).toBe('thawed')
    for (const value of [null, '', '   ', 42, {}, []]) expect(foodState(value)).toBeNull()
  })

  it('defaults description and tags to "" and keeps the description as sent', () => {
    const { description: _d, tags: _t, ...bare } = ovenHit
    expect(toSearchResponse(envelope([bare])).items[0]).toMatchObject({ description: '', tags: '' })
    expect(toSearchResponse(envelope([{ ...ovenHit, description: 42, tags: ['番茄'] }])).items[0]).toMatchObject({ description: '', tags: '' })
    expect(toSearchResponse(envelope([{ ...ovenHit, description: ' 留白 ' }])).items[0].description).toBe(' 留白 ')
  })

  it('maps the empty result and its message (P4)', () => {
    const empty = { ok: true, trace_id: 'trace-2', source: 'empty', scene: 'general', outputs_heating: false, items: [] }
    expect(toSearchResponse(empty)).toEqual({
      items: [], traceId: 'trace-2', message: '自建库暂未找到相关图片。', source: 'empty', scene: 'general', outputsHeating: false,
    })
    expect(toSearchResponse({ ...empty, message: '没有达到阈值的图片' }).message).toBe('没有达到阈值的图片')
  })

  it('rejects envelopes without an items array, including the predict result key (P3)', () => {
    for (const payload of [null, 'bad', [], {}, { items: null }, { items: {} }, { result: [ovenHit] }, { result: { results: [] } }]) {
      expect(() => toSearchResponse(payload)).toThrow('数据格式不正确')
    }
  })

  it('reads the card fields only where the card puts them (no pre-card top-level keys)', () => {
    const item = toSearchResponse(envelope([{
      id: 'x', image_url: 'https://legacy.example/a.png', thumb_url: 'https://legacy.example/t.png',
      title: '旧标题', category: '旧类别', similiar: 0.7, width: 10,
    }], { source: undefined })).items[0]
    expect(item).toMatchObject({
      id: 'image-1', imageUrl: null, thumbnailUrl: null, alternateUrls: [], title: '未命名图片 1', category: null,
      similarity: null, width: null, pageUrl: null, siteName: null,
    })
    expect(item.source).toBeUndefined()
    expect(getImageSources(item)).toEqual([])
  })

  it('keeps only real scores, safe URLs, objects and contract channels', () => {
    const scores = [null, '0.8', Number.NaN, Number.POSITIVE_INFINITY, 80, 0, -1, 1]
    const response = toSearchResponse(envelope([
      ...scores.map((similarity, index) => ({ similarity, details: { id: `s${index}` } })),
      null, 'bad', [], 1,
      { original_url: 'javascript:alert(1)', details: { images: [{ thumbnail_url: '//cdn.example/t.webp' }] } },
    ], { source: 'flyb', scene: 'device' }))
    expect(response.items.slice(0, scores.length).map(item => item.similarity)).toEqual([null, null, null, null, null, 0, -1, 1])
    expect(response.items).toHaveLength(scores.length + 1)
    expect(response.items.at(-1)).toMatchObject({ id: `image-${scores.length + 1}`, imageUrl: null, thumbnailUrl: 'https://cdn.example/t.webp' })
    expect(response.source).toBeUndefined()
    expect(response.items[0].source).toBeUndefined()
    expect(response.scene).toBe('oven')
  })
})
