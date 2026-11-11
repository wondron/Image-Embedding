import { describe, expect, it } from 'vitest'
import { describePayloadValue, payloadDiffRows, unavailableReason, type PayloadNames } from './vectorView'

const names: PayloadNames = {
  category: id => ({ 22: '食材 / 蔬菜 / 土豆', 23: '食材 / 蔬菜 / 胡萝卜' } as Record<number, string>)[id] ?? null,
  scene: code => ({ general: '通用（日常）', oven: '一体机' } as Record<string, string>)[code] ?? code,
  foodState: value => ({ raw: '生', cooked: '熟' } as Record<string, string>)[value] ?? value,
  labelStatus: value => ({ auto: '自动标注', verified: '已核验' } as Record<string, string>)[value] ?? value,
}

describe('describePayloadValue', () => {
  it('names categories, scenes and enum values instead of showing ids and codes', () => {
    expect(describePayloadValue('category_id', 23, names)).toBe('食材 / 蔬菜 / 胡萝卜')
    expect(describePayloadValue('category_id', 99, names)).toBe('类别 #99')  // not in the tree (deleted?)
    expect(describePayloadValue('scenes', ['general', 'oven', 'jp_diet'], names)).toBe('通用（日常）、一体机、jp_diet')
    expect(describePayloadValue('food_state', 'cooked', names)).toBe('熟')
    expect(describePayloadValue('label_status', 'verified', names)).toBe('已核验')
  })

  it('says what an empty value means for each field', () => {
    expect(describePayloadValue('category_id', null, names)).toBe('未设类别')
    expect(describePayloadValue('scenes', [], names)).toBe('无')
    expect(describePayloadValue('food_state', undefined, names)).toBe('未填写')
  })
})

describe('payloadDiffRows', () => {
  it('lists only the fields that lag, in words; scenes compare as a set', () => {
    const rows = payloadDiffRows({
      payload: { category_id: 22, scenes: ['oven', 'general'], food_state: 'raw', label_status: 'auto' },
      expectedPayload: { category_id: 23, scenes: ['general', 'oven'], food_state: 'cooked', label_status: 'auto' },
    }, names)
    expect(rows).toEqual([
      { key: 'category_id', label: '类别', stored: '食材 / 蔬菜 / 土豆', expected: '食材 / 蔬菜 / 胡萝卜' },
      { key: 'food_state', label: '食物状态', stored: '生', expected: '熟' },
    ])
  })

  it('has nothing to show without both payloads', () => {
    expect(payloadDiffRows({ payload: null, expectedPayload: { category_id: 1 } }, names)).toEqual([])
  })
})

describe('unavailableReason', () => {
  it('tells a service that is not up from a failed read', () => {
    expect(unavailableReason('retrieval stack not ready: embedding model unavailable')).toContain('检索服务没有就绪')
    expect(unavailableReason(null)).toContain('检索服务没有就绪')
    expect(unavailableReason('vector search failed')).toBe('读取向量库失败，可以稍后刷新再看。')
  })
})
