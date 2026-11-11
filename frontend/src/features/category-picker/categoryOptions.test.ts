import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { categoryOptions, categoryPath, filterCategoryOptions } from './categoryOptions'

function node(id: number, code: string, parentId: number | null, name: string | null, extra: Partial<CategoryNode> = {}): CategoryNode {
  return {
    id, code, parentId, path: String(id), name, names: name ? [{ lang: 'zh', name, kind: 'primary' }] : [], cuisines: [],
    heatingGroup: null, sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null, ...extra,
  }
}

const tree = buildCategoryTree([
  node(1, 'dish', null, '菜品'),
  node(2, 'dish.meat', 1, '肉类'),
  node(3, 'dish.meat.steak', 2, '牛排', { names: [{ lang: 'zh', name: '牛排', kind: 'primary' }, { lang: 'en', name: 'Steak', kind: 'alias' }] }),
  node(4, 'dish.meat.pork', 2, '红烧肉'),
  node(5, 'dish.old', 1, '旧类别', { isActive: false }),
  node(6, 'ingredient', null, null),
])

describe('categoryOptions', () => {
  it('lists every node in tree order with its path', () => {
    const options = categoryOptions(tree)
    expect(options.map(option => option.code)).toEqual(['dish', 'dish.meat', 'dish.meat.steak', 'dish.meat.pork', 'dish.old', 'ingredient'])
    expect(options[2]).toMatchObject({ label: '牛排', path: '菜品 / 肉类', depth: 2 })
    expect(options[5]).toMatchObject({ label: 'ingredient', path: '' })  // no zh name: the code
  })

  it('gives the full path of a node', () => {
    expect(categoryPath(tree, 3)).toBe('菜品 / 肉类 / 牛排')
    expect(categoryPath(tree, 99)).toBeNull()
  })
})

describe('filterCategoryOptions', () => {
  const options = categoryOptions(tree)
  const codes = (query: string) => filterCategoryOptions(options, query).map(option => option.code)

  it('keeps every active option in tree order for an empty query', () => {
    expect(codes('  ')).toEqual(['dish', 'dish.meat', 'dish.meat.steak', 'dish.meat.pork', 'ingredient'])
  })

  it('matches names, aliases, codes and paths, best matches first', () => {
    expect(codes('牛排')).toEqual(['dish.meat.steak'])
    expect(codes('steak')).toEqual(['dish.meat.steak'])  // alias and code
    expect(codes('肉')).toEqual(['dish.meat', 'dish.meat.pork', 'dish.meat.steak'])  // label hits before path-only hits
    expect(codes('菜品 牛')).toEqual(['dish.meat.steak'])  // every word must match
    expect(codes('旧')).toEqual([])  // disabled categories are not offered
  })
})
