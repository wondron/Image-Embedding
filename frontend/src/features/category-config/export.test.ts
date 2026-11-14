import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { buildCategoryTreeExport } from './export'

const node = (id: number, parentId: number | null, name: string, extra: Partial<CategoryNode> = {}): CategoryNode => ({
  id, parentId, code: `ingredient.Node${id}`, path: String(id), name,
  names: [{ lang: 'zh', name, kind: 'primary' }], cuisines: [], heatingGroup: null,
  sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0,
  createdAt: null, updatedAt: null, ...extra,
})
const now = new Date('2026-11-13T19:20:30.456Z')

describe('buildCategoryTreeExport', () => {
  it('keeps deep and disabled categories in tree order with exact metadata', () => {
    const nodes = [
      node(1, null, '食材', { code: 'ingredient' }),
      node(2, 1, '蔬菜'),
      node(3, 2, '根茎'),
      node(4, 3, '红薯', { isActive: false, path: '1.2.3.4', imageCount: 5, sceneRuleCount: 2 }),
      node(5, 1, '谷物'),
    ]
    const file = buildCategoryTreeExport(buildCategoryTree(nodes), ['ingredient'], [], now)
    const payload = JSON.parse(file.content)
    expect(file.count).toBe(5)
    expect(payload).toMatchObject({ schema_version: 1, exported_at: now.toISOString(), category_count: 5 })
    expect(payload.categories[0].children.map((entry: { id: number }) => entry.id)).toEqual([2, 5])
    expect(payload.categories[0].children[0].children[0].children[0]).toMatchObject({
      id: 4, parent_id: 3, code: 'ingredient.Node4', path: '1.2.3.4', name: '红薯',
      is_active: false, image_count: 5, scene_rule_count: 2, children: [],
    })
    expect(file.filename).toBe('category-tree-2026-11-13T19-20-30-456Z.json')
    expect(file.filename).not.toMatch(/[<>:"/\\|?*]/)
  })

  it('preserves aliases and own settings without replacing inherited empty values', () => {
    const group = { id: 8, code: 'grill', name: '烤' }
    const names: CategoryNode['names'] = [
      { lang: 'zh', name: '红薯', kind: 'primary' },
      { lang: 'zh', name: '番薯', kind: 'alias' },
      { lang: 'en', name: 'Sweet potato', kind: 'primary' },
    ]
    const nodes = [
      node(1, null, '食材', { cuisines: ['chinese'], heatingGroup: group }),
      node(2, 1, '红薯', { names, sortOrder: 12, createdAt: '2026-11-01T00:00:00Z', updatedAt: '2026-11-12T00:00:00Z' }),
    ]
    const before = JSON.stringify(nodes)
    const payload = JSON.parse(buildCategoryTreeExport(buildCategoryTree(nodes), ['ingredient', 'dish'], [group], now).content)
    expect(payload.root_codes).toEqual(['ingredient', 'dish'])
    expect(payload.heating_groups).toEqual([group])
    expect(payload.categories[0]).toMatchObject({ cuisines: ['chinese'], heating_group: group })
    expect(payload.categories[0].children[0]).toMatchObject({
      names, cuisines: [], heating_group: null, sort_order: 12,
      created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-12T00:00:00Z',
    })
    expect(JSON.stringify(nodes)).toBe(before)
  })

  it('exports an empty tree with count zero without altering the catalog arrays', () => {
    const rootCodes = Object.freeze(['ingredient'])
    const heatingGroups = Object.freeze([{ id: 1, code: 'steam', name: '蒸' }])
    const file = buildCategoryTreeExport(buildCategoryTree([]), rootCodes, heatingGroups, now)
    expect(file.count).toBe(0)
    expect(JSON.parse(file.content)).toMatchObject({ category_count: 0, categories: [], root_codes: ['ingredient'], heating_groups: [...heatingGroups] })
    expect(file.content).toContain('\n  "schema_version": 1,')
    expect(file.content.endsWith('\n')).toBe(true)
  })
})
