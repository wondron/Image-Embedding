import { describe, expect, it } from 'vitest'
import {
  INITIAL_LIBRARY_FILTERS,
  activeFilters,
  clearAllFilters,
  clearFilter,
  normalizeTag,
  pageCount,
  pageRange,
  withCategory,
  withLabelStatus,
  type FilterLabels,
  type LibraryFilters,
} from './model'

const filters = (patch: Partial<LibraryFilters> = {}): LibraryFilters => ({ ...INITIAL_LIBRARY_FILTERS, ...patch })
const labels: FilterLabels = { category: id => `类别${id}`, scene: code => (code === 'oven' ? '一体机' : code), defaultScene: 'general' }

describe('category and 「未标注」', () => {
  it('drops 「未标注」 when a category is picked: such an image has no category', () => {
    expect(withCategory(filters({ labelStatus: 'unlabeled' }), 3)).toEqual({ filters: filters({ categoryId: 3 }), dropped: 'labelStatus' })
    expect(withCategory(filters({ labelStatus: 'verified' }), 3)).toEqual({ filters: filters({ categoryId: 3, labelStatus: 'verified' }), dropped: null })
    expect(withCategory(filters({ labelStatus: 'unlabeled', categoryId: 3 }), null).dropped).toBeNull()
  })

  it('drops the category when 「未标注」 is picked', () => {
    expect(withLabelStatus(filters({ categoryId: 3 }), 'unlabeled')).toEqual({ filters: filters({ labelStatus: 'unlabeled' }), dropped: 'category' })
    expect(withLabelStatus(filters({ categoryId: 3 }), 'auto')).toEqual({ filters: filters({ categoryId: 3, labelStatus: 'auto' }), dropped: null })
    expect(withLabelStatus(filters({ categoryId: 3, labelStatus: 'auto' }), null).filters.categoryId).toBe(3)
  })
})

describe('normalizeTag', () => {
  it('trims and takes off the 「#」 the cards show in front of a tag', () => {
    expect(normalizeTag('  带骨 ')).toBe('带骨')
    expect(normalizeTag('#带骨')).toBe('带骨')
    expect(normalizeTag('# 带骨')).toBe('带骨')
    expect(normalizeTag('##x')).toBe('#x')  // a stored tag may itself start with 「#」
    expect(normalizeTag(' # ')).toBe('')
  })
})

describe('activeFilters', () => {
  it('lists nothing for the initial filters in the default scene', () => {
    expect(activeFilters(filters(), labels)).toEqual([])
    expect(activeFilters(filters({ query: '   ', tag: ' # ' }), labels)).toEqual([])
  })

  it('names each filter that narrows the list, in panel order', () => {
    const all = filters({ query: ' 牛 ', scene: 'oven', labelStatus: 'verified', outOfSync: true, tag: '#带骨', categoryId: 3 })
    expect(activeFilters(all, labels)).toEqual([
      { key: 'query', name: '关键词', value: '牛' },
      { key: 'scene', name: '场景', value: '一体机' },
      { key: 'labelStatus', name: '标注', value: '已核验' },
      { key: 'outOfSync', name: '向量', value: '待同步或没有向量' },
      { key: 'tag', name: '标签', value: '带骨' },
      { key: 'category', name: '类别', value: '类别3' },
    ])
  })

  it('does not call a scene a filter while the default one is unknown', () => {
    expect(activeFilters(filters({ scene: 'oven' }), { ...labels, defaultScene: null })).toEqual([])
  })
})

describe('clearing', () => {
  const all = filters({ query: '牛', scene: 'oven', labelStatus: 'auto', outOfSync: true, tag: '带骨', categoryId: 3 })

  it('clears one filter; the scene goes back to the default one', () => {
    expect(clearFilter(all, 'scene', 'general').scene).toBe('general')
    expect(clearFilter(all, 'scene', null).scene).toBe('oven')
    expect(clearFilter(all, 'category', 'general')).toEqual({ ...all, categoryId: null })
    expect(clearFilter(all, 'tag', 'general')).toEqual({ ...all, tag: '' })
    expect(clearFilter(all, 'outOfSync', 'general')).toEqual({ ...all, outOfSync: false })
    expect(clearFilter(all, 'labelStatus', 'general')).toEqual({ ...all, labelStatus: null })
    expect(clearFilter(all, 'query', 'general')).toEqual({ ...all, query: '' })
  })

  it('clears everything', () => {
    expect(clearAllFilters(all, 'general')).toEqual(filters({ scene: 'general' }))
    expect(clearAllFilters(all, null)).toEqual(filters({ scene: 'oven' }))
  })
})

describe('pages', () => {
  it('names the range a page shows', () => {
    expect(pageRange(1, 24, 0)).toBe('')
    expect(pageRange(1, 24, 10)).toBe('1–10')
    expect(pageRange(2, 24, 30)).toBe('25–30')
    expect(pageRange(3, 24, 30)).toBe('')  // past the end
  })

  it('counts at least one page', () => {
    expect([pageCount(0, 24), pageCount(24, 24), pageCount(25, 24), pageCount(97, 96)]).toEqual([1, 1, 2, 2])
  })
})
