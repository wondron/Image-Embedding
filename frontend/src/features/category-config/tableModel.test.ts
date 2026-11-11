import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryName, type CategoryNode } from '../../entities/category'
import { NO_CATEGORY_FILTER, categoryTableView, otherCategoryNames, retargetCode, type CategoryTableFilter } from './tableModel'

const zh = (name: string): CategoryName => ({ lang: 'zh', name, kind: 'primary' })
const node = (id: number, code: string, parentId: number | null, name: string, extra: Partial<CategoryNode> = {}): CategoryNode => ({
  id, code, parentId, path: String(id), name, names: [zh(name)], cuisines: [], heatingGroup: null,
  sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null, ...extra,
})
/** 食材(1) > 蔬菜(2) > 西红柿(3, alias 番茄), 土豆(4, inactive); 食材 > 肉禽(5) > 牛肉(6); 菜品(7) > 牛肉面(8) */
const tree = buildCategoryTree([
  node(1, 'ingredient', null, '食材'),
  node(7, 'dish', null, '菜品'),
  node(2, 'ingredient.vegetable', 1, '蔬菜'),
  node(5, 'ingredient.meat', 1, '肉禽'),
  node(8, 'dish.noodle_beef', 7, '牛肉面'),
  node(3, 'ingredient.vegetable.tomato', 2, '西红柿', { names: [zh('西红柿'), { lang: 'zh', name: '番茄', kind: 'alias' }, { lang: 'en', name: 'Tomato', kind: 'primary' }] }),
  node(4, 'ingredient.vegetable.potato', 2, '土豆', { isActive: false }),
  node(6, 'ingredient.meat.beef', 5, '牛肉'),
])
const ids = (filter: Partial<CategoryTableFilter>, expanded: number[] = []) =>
  categoryTableView(tree, { ...NO_CATEGORY_FILTER, ...filter }, new Set(expanded)).rows.map(row => row.entry.node.id)

describe('categoryTableView', () => {
  it('lists a node\'s children only while it is expanded, in tree order with depths', () => {
    expect(ids({})).toEqual([1, 7])
    const view = categoryTableView(tree, NO_CATEGORY_FILTER, new Set([1, 2]))
    expect(view.rows.map(row => [row.entry.node.id, row.depth, row.open])).toEqual([
      [1, 0, true], [2, 1, true], [3, 2, false], [4, 2, false], [5, 1, false], [7, 0, false],
    ])
    expect(view).toMatchObject({ total: 8, matched: 8, filtering: false })
    // A leaf is never "open", even if its id is in the expanded set
    expect(categoryTableView(tree, NO_CATEGORY_FILTER, new Set([7, 8])).rows.find(row => row.entry.node.id === 8)?.open).toBe(false)
  })

  it('a search lists every match with its ancestors as context, opened, whatever was expanded', () => {
    const view = categoryTableView(tree, { ...NO_CATEGORY_FILTER, query: '牛肉' }, new Set())
    expect(view.rows.map(row => [row.entry.node.id, row.context, row.open])).toEqual([
      [1, true, true], [5, true, true], [6, false, false], [7, true, true], [8, false, false],
    ])
    expect(view).toMatchObject({ total: 8, matched: 2, filtering: true })
  })

  it('matches aliases, other languages and codes, case-insensitively; every word must match', () => {
    expect(ids({ query: '番茄' })).toEqual([1, 2, 3])
    expect(ids({ query: 'tomato' })).toEqual([1, 2, 3])
    expect(ids({ query: 'VEGETABLE.potato' })).toEqual([1, 2, 4])
    expect(ids({ query: '牛肉 noodle' })).toEqual([7, 8])
    expect(ids({ query: '没有这个' })).toEqual([])
  })

  it('a match does not drag in its non-matching children', () => {
    expect(ids({ query: '蔬菜' })).toEqual([1, 2])
  })

  it('filters by status, keeping the ancestors for context', () => {
    const view = categoryTableView(tree, { ...NO_CATEGORY_FILTER, status: 'inactive' }, new Set())
    expect(view.rows.map(row => [row.entry.node.id, row.context])).toEqual([[1, true], [2, true], [4, false]])
    expect(view.matched).toBe(1)
    expect(ids({ status: 'active', query: '土豆' })).toEqual([])
  })

  it('a domain limits the table and the counts to that root\'s subtree', () => {
    expect(ids({ domainId: 7 }, [7])).toEqual([7, 8])
    expect(categoryTableView(tree, { ...NO_CATEGORY_FILTER, domainId: 1 }, new Set()).total).toBe(6)
    expect(ids({ domainId: 1, query: '牛肉' })).toEqual([1, 5, 6])
  })
})

describe('otherCategoryNames', () => {
  it('drops the zh primary name (the label) and keeps the rest in order', () => {
    const names: CategoryName[] = [zh('西红柿'), { lang: 'zh', name: '番茄', kind: 'alias' }, { lang: 'en', name: 'Tomato', kind: 'primary' }]
    expect(otherCategoryNames(names)).toEqual(names.slice(1))
    expect(otherCategoryNames([{ lang: 'en', name: 'Tomato', kind: 'primary' }])).toHaveLength(1)
  })
})

describe('retargetCode', () => {
  it('moves what was typed after the old prefix under the new one', () => {
    expect(retargetCode('dish.pizza', 'dish.', 'ingredient.')).toBe('ingredient.pizza')
    expect(retargetCode('dish.', 'dish.', 'ingredient.vegetable.')).toBe('ingredient.vegetable.')
    expect(retargetCode('', '', 'dish.')).toBe('dish.')
  })

  it('keeps a code that does not follow the old prefix', () => {
    expect(retargetCode('tomato', 'dish.', 'ingredient.')).toBe('tomato')
    expect(retargetCode('tomato', '', 'ingredient.')).toBe('tomato')
  })
})
