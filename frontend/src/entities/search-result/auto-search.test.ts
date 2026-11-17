import { describe, expect, it } from 'vitest'
import { getImageSources, getThumbnailSources, hasImage, toAutoSearchResponse } from '.'

/** A self-built hit, as POST /api/search/auto/* sends it: the shared card plus its own `source`. */
const selfHit = {
  tags: '牛排、带骨',
  description: '煎牛排',
  original_url: 'https://s3.example/original/1.jpg',
  similarity: 0.83,
  details: {
    id: '0b6f2c1e-0000-4000-8000-000000000001',
    category_id: 12,
    category: '牛排',
    scenes: ['general', 'oven'],
    scene_label: '烤牛排',
    label_status: 'verified',
    images: [{ image_url: 'https://s3.example/original/1.jpg', thumbnail_url: 'https://s3.example/thumb/1.webp', width: 800, height: 600 }],
    food_state: 'raw',
    heating_group: { id: 2, code: 'grill', name: '烤' },
  },
  source: 'self',
}
/** 菜谱识别's card: the dish names as its tags, every other card field empty. */
const recipeCard = { tags: '荷塘小炒、凉拌猪耳、清蒸鲈鱼', description: '', original_url: null, similarity: null, details: {}, source: 'recipe' }
const globalCard = {
  tags: '清蒸鲈鱼的做法',
  description: '鲈鱼洗净',
  original_url: 'https://img.example/fish.jpg',
  similarity: null,
  details: { rank: 1, page_url: 'https://site.example/fish', site: { Hostname: '美食站' }, images: [{ image_url: 'https://img.example/fish.jpg', thumbnail_url: 'https://img.example/fish-s.jpg', width: 640, height: 480 }], document_info: null },
  source: 'global',
}
const envelope = (source: string, items: unknown[], extra: Record<string, unknown> = {}) => ({
  ok: true, trace_id: 'trace-1', source, scene: 'oven', outputs_heating: true, items, fallback: { status: 'not_needed' }, ...extra,
})

describe('toAutoSearchResponse (§6.6)', () => {
  it('reads each card by its own source: the self-built hits, then the recipe card', () => {
    const response = toAutoSearchResponse(envelope('self', [selfHit, recipeCard]))
    expect(response).toMatchObject({ source: 'self', scene: 'oven', outputsHeating: true, traceId: 'trace-1', fallback: { status: 'not_needed' } })
    expect(response.items).toHaveLength(2)
    expect(response.items[0]).toMatchObject({
      id: '0b6f2c1e-0000-4000-8000-000000000001', source: 'self', title: '烤牛排', tags: '牛排、带骨', category: '牛排',
      similarity: 0.83, thumbnailUrl: 'https://s3.example/thumb/1.webp', outputsHeating: true,
      heatingGroup: { id: 2, code: 'grill', name: '烤' },
    })
    // The dish names are the card's tags and its title; it stands for no image
    expect(response.items[1]).toEqual({
      id: 'recipe-2', source: 'recipe', title: '荷塘小炒、凉拌猪耳、清蒸鲈鱼', tags: '荷塘小炒、凉拌猪耳、清蒸鲈鱼', description: '',
      imageUrl: null, thumbnailUrl: null, alternateUrls: [], similarity: null,
      categoryId: null, category: null, scenes: [], sceneLabel: null, labelStatus: null,
      foodState: null, heatingGroup: null, width: null, height: null, outputsHeating: false,
      pageUrl: null, siteName: null,
    })
  })

  it('reads the recipe card alone (the self-built library had nothing) and never gives it an image', () => {
    const response = toAutoSearchResponse(envelope('recipe', [
      { ...recipeCard, original_url: 'https://img.example/not-a-recipe-image.jpg', similarity: 0.5 },
    ], { outputs_heating: false }))
    expect(response).toMatchObject({ source: 'recipe', outputsHeating: false })
    const [card] = response.items
    expect(card).toMatchObject({ id: 'recipe-1', source: 'recipe', imageUrl: null, similarity: null })
    expect(hasImage(card)).toBe(false)
    expect(getImageSources(card)).toEqual([])
    expect(getThumbnailSources(card)).toEqual([])
    expect(response.message).toBeUndefined()
  })

  it('reads Global results by their own source and without heating information', () => {
    const response = toAutoSearchResponse(envelope('global', [globalCard], { outputs_heating: false, fallback: { status: 'hit' } }))
    expect(response).toMatchObject({ source: 'global', fallback: { status: 'hit' } })
    expect(response.items[0]).toMatchObject({
      id: 'image-1', source: 'global', title: '清蒸鲈鱼的做法', imageUrl: 'https://img.example/fish.jpg',
      pageUrl: 'https://site.example/fish', siteName: '美食站', category: null, outputsHeating: false,
    })
    expect(hasImage(response.items[0])).toBe(true)
  })

  it('falls back to the envelope source for cards without one (a server older than 菜谱识别)', () => {
    const { source: _self, ...oldSelfHit } = selfHit
    const { source: _global, ...oldGlobalCard } = globalCard
    expect(toAutoSearchResponse(envelope('self', [oldSelfHit])).items[0]).toMatchObject({ source: 'self', category: '牛排' })
    expect(toAutoSearchResponse(envelope('global', [oldGlobalCard])).items[0]).toMatchObject({ source: 'global', siteName: '美食站' })
    // An unknown card source is treated as not sent
    expect(toAutoSearchResponse(envelope('self', [{ ...selfHit, source: 'flyb' }])).items[0]).toMatchObject({ source: 'self' })
  })

  it('explains an empty result, adding the reason of a failed fallback', () => {
    const said = '自建库没有达到阈值的结果，菜谱识别没有识别到菜名，Global 兜底失败'
    const failed = toAutoSearchResponse(envelope('empty', [], {
      message: said, fallback: { status: 'failed', error: 'UPSTREAM_TIMEOUT', detail: '上游搜索服务响应超时' },
    }))
    expect(failed).toMatchObject({ source: 'empty', items: [], fallback: { status: 'failed', error: 'UPSTREAM_TIMEOUT' } })
    expect(failed.message).toBe(`${said}：上游搜索服务响应超时（UPSTREAM_TIMEOUT）`)
    const disabled = toAutoSearchResponse(envelope('empty', [], { message: '自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底', fallback: { status: 'disabled' } }))
    expect(disabled.message).toBe('自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底')
  })

  it('rejects a body without an items array', () => {
    expect(() => toAutoSearchResponse({ ok: true, source: 'recipe' })).toThrow('格式')
    expect(() => toAutoSearchResponse(null)).toThrow('格式')
  })
})
