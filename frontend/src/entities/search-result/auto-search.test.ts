import { describe, expect, it } from 'vitest'
import { getImageSources, getThumbnailSources, hasImage, toAutoSearchResponse } from '.'

/**
 * A self-built hit, as POST /api/search/auto/* sends it: its own `source`, then the shared card;
 * its details end with `outputs_heating`, whether they carry the heating information.
 */
const selfHit = {
  source: 'self',
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
    outputs_heating: true,
  },
}
/** 菜谱识别's card: the dish names as its tags, every other card field empty. */
const recipeCard = { source: 'recipe', tags: '荷塘小炒、凉拌猪耳、清蒸鲈鱼', description: '', original_url: null, similarity: null, details: {} }
const globalCard = {
  source: 'global',
  tags: '清蒸鲈鱼的做法',
  description: '鲈鱼洗净',
  original_url: 'https://img.example/fish.jpg',
  similarity: null,
  details: { rank: 1, page_url: 'https://site.example/fish', site: { Hostname: '美食站' }, images: [{ image_url: 'https://img.example/fish.jpg', thumbnail_url: 'https://img.example/fish-s.jpg', width: 640, height: 480 }], document_info: null },
}
/** The envelope says neither where the cards came from nor whether they carry heating information. */
const envelope = (items: unknown[], extra: Record<string, unknown> = {}) => ({
  ok: true, trace_id: 'trace-1', scene: 'oven', items, fallback: { status: 'not_needed' }, ...extra,
})

describe('toAutoSearchResponse (§6.6)', () => {
  it('reads each card by its own source: the self-built hits, then the recipe card', () => {
    const response = toAutoSearchResponse(envelope([selfHit, recipeCard]))
    // The answer's channel is summed up from the cards: self-built hits, whatever follows them
    expect(response).toMatchObject({ source: 'self', scene: 'oven', traceId: 'trace-1', fallback: { status: 'not_needed' } })
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

  it('takes the heating flag from each self-built card, not from the envelope', () => {
    const plainHit = { ...selfHit, details: { ...selfHit.details, outputs_heating: false } }
    const response = toAutoSearchResponse(envelope([selfHit, plainHit, recipeCard]))
    expect(response.items.map(item => item.outputsHeating)).toEqual([true, false, false])
    expect(response.outputsHeating).toBeUndefined()
  })

  it('keeps which box of the query image found a region-search hit; a whole-image hit has none', () => {
    const regionHit = (region: unknown) => ({ ...selfHit, details: { ...selfHit.details, region } })
    const response = toAutoSearchResponse(envelope([
      regionHit({ label: 'food', score: 0.6213, box: [25, 40, 300, 280] }),
      selfHit,
      recipeCard,
    ]))
    expect(response.source).toBe('self')
    expect(response.items.map(item => item.region)).toEqual([{ label: 'food', score: 0.6213, box: [25, 40, 300, 280] }, undefined, undefined])
    // the rest of the card is read as before
    expect(response.items[0]).toMatchObject({ category: '牛排', similarity: 0.83, outputsHeating: true })
    // anything that is not a box is left out, not guessed
    for (const broken of [
      null, 'food', { label: 'food', score: 1.5, box: [0, 0, 10, 10] }, { label: 'food', score: 0.6, box: [0, 0, 10] },
      { label: 'food', score: 0.6, box: [10, 0, 10, 10] }, { label: 'food', score: 0.6, box: [-1, 0, 10, 10] },
    ]) expect(toAutoSearchResponse(envelope([regionHit(broken)])).items[0].region).toBeUndefined()
    // a box without a word still says where it was
    expect(toAutoSearchResponse(envelope([regionHit({ score: 0.5, box: [0, 0, 10, 10] })])).items[0].region)
      .toEqual({ label: '', score: 0.5, box: [0, 0, 10, 10] })
  })

  it('reads the recipe card alone (the self-built library had nothing) and never gives it an image', () => {
    const response = toAutoSearchResponse(envelope([
      { ...recipeCard, original_url: 'https://img.example/not-a-recipe-image.jpg', similarity: 0.5 },
    ]))
    expect(response).toMatchObject({ source: 'recipe' })
    const [card] = response.items
    expect(card).toMatchObject({ id: 'recipe-1', source: 'recipe', imageUrl: null, similarity: null, outputsHeating: false })
    expect(hasImage(card)).toBe(false)
    expect(getImageSources(card)).toEqual([])
    expect(getThumbnailSources(card)).toEqual([])
    expect(response.message).toBeUndefined()
  })

  it('reads Global results by their own source and without heating information', () => {
    const response = toAutoSearchResponse(envelope([globalCard], { fallback: { status: 'hit' } }))
    expect(response).toMatchObject({ source: 'global', fallback: { status: 'hit' } })
    expect(response.items[0]).toMatchObject({
      id: 'image-1', source: 'global', title: '清蒸鲈鱼的做法', imageUrl: 'https://img.example/fish.jpg',
      pageUrl: 'https://site.example/fish', siteName: '美食站', category: null, outputsHeating: false,
    })
    expect(hasImage(response.items[0])).toBe(true)
  })

  it('treats an unknown card source as not sent', () => {
    const response = toAutoSearchResponse(envelope([{ ...selfHit, source: 'flyb' }]))
    expect(response.items[0]).toMatchObject({ category: '牛排', outputsHeating: true })
    expect(response.items[0].source).toBeUndefined()
    expect(response.source).toBeUndefined()
  })

  it('still reads an older server, which said the source and the heating flag on the envelope', () => {
    const { outputs_heating: _flag, ...oldDetails } = selfHit.details
    const { source: _self, ...oldSelfCard } = selfHit
    const oldSelfHit = { ...oldSelfCard, details: oldDetails }
    const { source: _global, ...oldGlobalCard } = globalCard
    const old = (source: string, outputs_heating: boolean, items: unknown[]) => envelope(items, { source, outputs_heating })

    // Cards without a source of their own (a server older than 菜谱识别) take the envelope's
    const self = toAutoSearchResponse(old('self', true, [oldSelfHit]))
    expect(self).toMatchObject({ source: 'self', items: [{ source: 'self', category: '牛排', outputsHeating: true }] })
    expect(toAutoSearchResponse(old('global', false, [oldGlobalCard])).items[0]).toMatchObject({ source: 'global', siteName: '美食站' })
    expect(toAutoSearchResponse(old('self', false, [{ ...oldSelfHit, source: 'self' }, recipeCard])).items)
      .toMatchObject([{ source: 'self', outputsHeating: false }, { source: 'recipe' }])
    expect(toAutoSearchResponse(old('empty', true, []))).toMatchObject({ source: 'empty', items: [] })
    // What a card says itself comes first
    expect(toAutoSearchResponse(old('self', false, [selfHit])).items[0]).toMatchObject({ source: 'self', outputsHeating: true })
  })

  it('explains an empty result, adding the reason of a failed fallback', () => {
    const said = '自建库没有达到阈值的结果，菜谱识别没有识别到菜名，Global 兜底失败'
    const failed = toAutoSearchResponse(envelope([], {
      message: said, fallback: { status: 'failed', error: 'UPSTREAM_TIMEOUT', detail: '上游搜索服务响应超时' },
    }))
    // No card: the answer is empty, and the fallback says why
    expect(failed).toMatchObject({ source: 'empty', items: [], fallback: { status: 'failed', error: 'UPSTREAM_TIMEOUT' } })
    expect(failed.message).toBe(`${said}：上游搜索服务响应超时（UPSTREAM_TIMEOUT）`)
    const disabled = toAutoSearchResponse(envelope([], { message: '自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底', fallback: { status: 'disabled' } }))
    expect(disabled).toMatchObject({ source: 'empty', fallback: { status: 'disabled' } })
    expect(disabled.message).toBe('自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底')
    // Without a message of the server's, the usual one
    expect(toAutoSearchResponse(envelope([], { fallback: { status: 'disabled' } })).message).toBe('自建库暂未找到相关图片。')
  })

  it('rejects a body without an items array', () => {
    expect(() => toAutoSearchResponse({ ok: true, trace_id: 'trace-1', scene: 'oven', fallback: { status: 'not_needed' } })).toThrow('格式')
    expect(() => toAutoSearchResponse(null)).toThrow('格式')
  })
})
