import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { pickerIndex, pickerRows, type PickerScope } from './pickerRows'

function node(id: number, parentId: number | null, name: string, isActive = true): CategoryNode {
  return {
    id, code: `c${id}`, parentId, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }], cuisines: [],
    heatingGroup: null, sortOrder: 0, isActive, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
  }
}

// 菜品(1) > 肉类(2) > 牛排(3), 红烧肉(4); 菜品 > 旧类别(5, off) > 旧菜(8); 菜品 > 停用叶(9, off); 食材(6); 饮品(7, off)
const tree = buildCategoryTree([
  node(1, null, '菜品'),
  node(6, null, '食材'),
  node(7, null, '饮品', false),
  node(2, 1, '肉类'),
  node(5, 1, '旧类别', false),
  node(9, 1, '停用叶', false),
  node(3, 2, '牛排'),
  node(4, 2, '红烧肉'),
  node(8, 5, '旧菜'),
])
const names = (scope: PickerScope, open: number[]) => pickerRows(pickerIndex(tree, scope), id => open.includes(id))
  .map(row => `${'  '.repeat(row.depth)}${row.node.name}${row.pickable ? '' : '*'}`)

describe('pickerIndex', () => {
  it('offers the enabled categories, and keeps a disabled one only above an enabled one', () => {
    const index = pickerIndex(tree)
    expect([...index.pickable].sort()).toEqual([1, 2, 3, 4, 6, 8])
    expect(index.roots.map(root => root.node.id)).toEqual([1, 6])  // 饮品: disabled, nothing below
    expect(index.children.get(1)?.map(child => child.node.id)).toEqual([2, 5])  // 停用叶 is left out
    expect(index.pickable.has(5)).toBe(false)  // listed for 旧菜, not offered itself
    expect([index.below.get(1), index.below.get(2), index.below.get(5), index.below.get(6)]).toEqual([4, 2, 1, 0])
  })

  it('offers disabled categories too when asked (a parent, a rule target)', () => {
    const index = pickerIndex(tree, { includeInactive: true })
    expect(index.pickable.size).toBe(9)
    expect(index.roots.map(root => root.node.id)).toEqual([1, 6, 7])
    expect(index.below.get(1)).toBe(6)
  })

  it('leaves a node out together with everything below it', () => {
    const index = pickerIndex(tree, { exclude: new Set([2]) })
    expect([...index.pickable].sort()).toEqual([1, 6, 8])
    expect(index.children.get(1)?.map(child => child.node.id)).toEqual([5])
    expect(index.below.get(1)).toBe(1)
    expect(pickerIndex(tree, { exclude: new Set([1, 6]) }).roots).toEqual([])
  })
})

describe('pickerRows', () => {
  it('lists a node\'s children only while it is open, in tree order', () => {
    expect(names({}, [])).toEqual(['菜品', '食材'])
    expect(names({}, [1])).toEqual(['菜品', '  肉类', '  旧类别*', '食材'])
    expect(names({}, [1, 2, 5])).toEqual(['菜品', '  肉类', '    牛排', '    红烧肉', '  旧类别*', '    旧菜', '食材'])
    // A closed parent hides its subtree even when a child is marked open
    expect(names({}, [2])).toEqual(['菜品', '食材'])
    expect(names({ includeInactive: true }, [1])).toEqual(['菜品', '  肉类', '  旧类别', '  停用叶', '食材', '饮品'])
  })

  it('says what each line needs to be drawn and moved through', () => {
    const rows = pickerRows(pickerIndex(tree), id => id === 1)
    expect(rows.map(row => [row.node.id, row.parentId, row.hasChildren, row.open, row.below, row.position, row.siblings])).toEqual([
      [1, null, true, true, 4, 1, 2],
      [2, 1, true, false, 2, 1, 2],
      [5, 1, true, false, 1, 2, 2],
      [6, null, false, false, 0, 2, 2],
    ])
  })

  it('folds a catalog of thousands to the levels that are open', () => {
    const nodes: CategoryNode[] = [node(1, null, '食材')]
    for (let group = 0; group < 50; group += 1) {
      nodes.push(node(100 + group, 1, `大类${group}`))
      for (let leaf = 0; leaf < 60; leaf += 1) nodes.push(node(10_000 + group * 100 + leaf, 100 + group, `食材${group}-${leaf}`, leaf % 7 !== 0))
    }
    const index = pickerIndex(buildCategoryTree(nodes))
    expect(pickerRows(index, () => true)).toHaveLength(index.pickable.size)
    expect(pickerRows(index, id => id === 1)).toHaveLength(51)  // the root and its groups, not 3000 lines
  })
})
