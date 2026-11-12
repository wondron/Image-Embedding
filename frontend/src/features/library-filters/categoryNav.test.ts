import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { categoryNavView, highlightParts, subtreeImageCounts, type CategoryNavOptions } from './categoryNav'

function node(id: number, parentId: number | null, name: string, extra: Partial<CategoryNode> = {}): CategoryNode {
  return {
    id, code: `c${id}`, parentId, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }],
    cuisines: [], heatingGroup: null, sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0,
    createdAt: null, updatedAt: null, ...extra,
  }
}

// 菜品 > 肉类 > 牛排 (3) / 红烧肉 (1); 菜品 > 汤 (0); 饮品 (disabled) > 茶 (2)
const tree = buildCategoryTree([
  node(1, null, '菜品'),
  node(2, 1, '肉类', { imageCount: 1 }),
  node(3, 2, '牛排', { imageCount: 3, names: [{ lang: 'zh', name: '牛排', kind: 'primary' }, { lang: 'en', name: 'steak', kind: 'alias' }] }),
  node(4, 2, '红烧肉', { imageCount: 1 }),
  node(5, 1, '汤'),
  node(6, null, '饮品', { isActive: false }),
  node(7, 6, '茶', { imageCount: 2 }),
])
const counts = subtreeImageCounts(tree)
const base: CategoryNavOptions = { query: '', expanded: new Set(), hideEmpty: false, showInactive: false }
const view = (options: Partial<CategoryNavOptions>) => categoryNavView(tree, counts, { ...base, ...options })
const labels = (options: Partial<CategoryNavOptions>) => view(options).rows.map(row => `${'  '.repeat(row.depth)}${row.node.name}${row.context ? '*' : ''}`)

describe('subtreeImageCounts', () => {
  it('adds the images below a node to its own', () => {
    expect(Object.fromEntries(counts)).toEqual({ 1: 5, 2: 5, 3: 3, 4: 1, 5: 0, 6: 2, 7: 2 })
  })
})

describe('categoryNavView', () => {
  it('lists the roots, and the children of the expanded nodes only', () => {
    expect(labels({})).toEqual(['菜品', '饮品*'])
    expect(labels({ expanded: new Set([1, 2]) })).toEqual(['菜品', '  肉类', '    牛排', '    红烧肉', '  汤', '饮品*'])
    // A collapsed parent hides its subtree even when a child is expanded
    expect(labels({ expanded: new Set([2]) })).toEqual(['菜品', '饮品*'])
  })

  it('keeps a disabled parent as context of an enabled child, and lists it for itself on request', () => {
    expect(labels({ expanded: new Set([6]) })).toEqual(['菜品', '饮品*', '  茶'])
    expect(labels({ expanded: new Set([6]), showInactive: true })).toEqual(['菜品', '饮品', '  茶'])
    const disabledLeaf = buildCategoryTree([node(1, null, '菜品'), node(2, 1, '停用', { isActive: false })])
    expect(categoryNavView(disabledLeaf, subtreeImageCounts(disabledLeaf), { ...base, expanded: new Set([1]) }).rows.map(row => row.node.name)).toEqual(['菜品'])
  })

  it('leaves out the categories without images when asked', () => {
    expect(labels({ expanded: new Set([1, 2]), hideEmpty: true })).toEqual(['菜品', '  肉类', '    牛排', '    红烧肉', '饮品*'])
  })

  it('lists the matches with their ancestors open, and nothing else', () => {
    const found = view({ query: '牛' })
    expect(found.rows.map(row => row.node.name)).toEqual(['菜品', '肉类', '牛排'])
    expect(found.rows.map(row => row.context)).toEqual([true, true, false])
    expect(found.rows.map(row => row.open)).toEqual([true, true, false])
    expect([found.searching, found.matched, found.hidden]).toEqual([true, 1, 0])
    // Aliases and codes match too; case does not matter
    expect(view({ query: 'STEAK' }).rows.at(-1)?.node.name).toBe('牛排')
    expect(view({ query: 'c4' }).rows.at(-1)?.node.name).toBe('红烧肉')
  })

  it('matches extra words against the ancestors, but never the ancestors alone', () => {
    expect(view({ query: '肉类 牛' }).rows.map(row => row.node.name)).toEqual(['菜品', '肉类', '牛排'])
    // 「菜品」 finds the root, not its whole subtree
    expect(view({ query: '菜品' }).rows.map(row => row.node.name)).toEqual(['菜品'])
    expect(view({ query: '饮品 牛' }).rows).toEqual([])
  })

  it('counts the matches the options leave out', () => {
    const found = view({ query: '汤', hideEmpty: true })
    expect([found.rows.length, found.matched, found.hidden]).toEqual([0, 0, 1])
    expect(view({ query: '饮品' })).toMatchObject({ matched: 0, hidden: 1 })
    expect(view({ query: '饮品', showInactive: true })).toMatchObject({ matched: 1, hidden: 0 })
  })

  it('gives each row its position among the listed siblings', () => {
    const rows = view({ expanded: new Set([1, 2]), hideEmpty: true }).rows
    expect(rows.map(row => [row.node.name, row.position, row.siblings, row.parentId])).toEqual([
      ['菜品', 1, 2, null], ['肉类', 1, 1, 1], ['牛排', 1, 2, 2], ['红烧肉', 2, 2, 2], ['饮品', 2, 2, null],
    ])
    expect(rows.map(row => [row.hasChildren, row.count])).toEqual([[true, 5], [true, 5], [false, 3], [false, 1], [true, 2]])
  })

  it('stays fast on a tree of thousands of categories', () => {
    const nodes: CategoryNode[] = [node(1, null, '食材')]
    for (let group = 0; group < 50; group += 1) {
      const groupId = 100 + group
      nodes.push(node(groupId, 1, `大类${group}`))
      for (let leaf = 0; leaf < 60; leaf += 1) nodes.push(node(10_000 + group * 100 + leaf, groupId, `食材${group}-${leaf}`, { imageCount: leaf % 3 }))
    }
    const big = buildCategoryTree(nodes)
    const bigCounts = subtreeImageCounts(big)
    const started = performance.now()
    const found = categoryNavView(big, bigCounts, { ...base, query: '食材1' })
    expect(performance.now() - started).toBeLessThan(200)
    expect(found.matched).toBeGreaterThan(600)
    expect(categoryNavView(big, bigCounts, { ...base, expanded: new Set([1]) }).rows).toHaveLength(51)
  })
})

describe('highlightParts', () => {
  it('marks every occurrence of every word', () => {
    expect(highlightParts('红烧肉烧', ['烧'])).toEqual([
      { text: '红', hit: false }, { text: '烧', hit: true }, { text: '肉', hit: false }, { text: '烧', hit: true },
    ])
    expect(highlightParts('Steak', ['st', 'ak'])).toEqual([{ text: 'St', hit: true }, { text: 'e', hit: false }, { text: 'ak', hit: true }])
  })

  it('leaves the text whole without words, and ignores empty ones', () => {
    expect(highlightParts('牛排', [])).toEqual([{ text: '牛排', hit: false }])
    expect(highlightParts('牛排', [''])).toEqual([{ text: '牛排', hit: false }])
    expect(highlightParts('', ['牛'])).toEqual([{ text: '', hit: false }])
  })
})
