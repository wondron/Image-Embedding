import { describe, expect, it } from 'vitest'
import { buildCategoryTree, flattenCategoryTree, type CategoryNode } from '../../entities/category'
import { categoryRole, categoryStats, matchesFocus, scopeEntries, type CategoryFocus } from './stats'

const node = (id: number, parentId: number | null, name: string, extra: Partial<CategoryNode> = {}): CategoryNode => ({
  id, code: `c${id}`, parentId, path: String(id), name, names: [], cuisines: [], heatingGroup: null,
  sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null, ...extra,
})
/**
 * 食材(1) > 蔬菜(2, 5 images) > 西红柿(3, 12), 土豆(4, none, disabled); 食材 > 肉禽(5) > 牛肉(6, 3);
 * 菜品(7, 1 image) > 牛肉面(8, none); 饮品(9): a domain with nothing below it yet
 */
const tree = buildCategoryTree([
  node(1, null, '食材'),
  node(7, null, '菜品', { imageCount: 1 }),
  node(9, null, '饮品'),
  node(2, 1, '蔬菜', { imageCount: 5 }),
  node(5, 1, '肉禽'),
  node(8, 7, '牛肉面'),
  node(3, 2, '西红柿', { imageCount: 12 }),
  node(4, 2, '土豆', { isActive: false }),
  node(6, 5, '牛肉', { imageCount: 3 }),
])
const entry = (id: number) => tree.byId.get(id)!

describe('categoryStats', () => {
  it('counts domains, groups and leaves (a root is a domain even with nothing below it), and where the images are', () => {
    expect(categoryStats(flattenCategoryTree(tree))).toEqual({
      total: 9, domains: 3, groups: 2, leaves: 4, inactive: 1, levels: 3,
      images: 21, leafImages: 15, coarseImages: 6, coarseCategories: 2, emptyLeaves: 2, fewImageLeaves: 1,
    })
  })

  it('counts a scope from its own top: no domains below a group, levels relative to it', () => {
    expect(categoryStats(scopeEntries(tree, 2))).toEqual({
      total: 3, domains: 0, groups: 1, leaves: 2, inactive: 1, levels: 2,
      images: 17, leafImages: 12, coarseImages: 5, coarseCategories: 1, emptyLeaves: 1, fewImageLeaves: 0,
    })
  })

  it('counts nothing for a scope that is no longer in the tree', () => {
    expect(scopeEntries(tree, 99)).toEqual([])
    expect(categoryStats([])).toMatchObject({ total: 0, levels: 0, images: 0 })
  })
})

describe('categoryRole and matchesFocus', () => {
  it('tells domains, groups and leaves apart', () => {
    expect([1, 9, 2, 3].map(id => categoryRole(entry(id)))).toEqual(['domain', 'domain', 'group', 'leaf'])
  })

  it('picks the categories each footer count is about', () => {
    const matching = (focus: CategoryFocus) => flattenCategoryTree(tree).filter(item => matchesFocus(item, focus)).map(item => item.node.id)
    expect(matching('empty_leaves')).toEqual([4, 8])
    expect(matching('few_image_leaves')).toEqual([6])
    expect(matching('coarse_images')).toEqual([2, 7])
  })
})
