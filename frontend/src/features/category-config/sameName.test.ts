import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryName, type CategoryNode } from '../../entities/category'
import { sameNameCategories } from './sameName'

const zh = (name: string, kind: CategoryName['kind'] = 'primary'): CategoryName => ({ lang: 'zh', name, kind })
const node = (id: number, parentId: number | null, name: string, names: CategoryName[] = [zh(name)]): CategoryNode => ({
  id, code: `c${id}`, parentId, path: String(id), name, names, cuisines: [], heatingGroup: null,
  sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
})

// 食材(1) > 薯类(2) > 红薯(3, alias 番薯 / 地瓜); 菜品(4) > 红薯(5)
const tree = buildCategoryTree([
  node(1, null, '食材'),
  node(2, 1, '薯类'),
  node(3, 2, '红薯', [zh('红薯'), zh('番薯', 'alias'), zh('地瓜', 'alias'), { lang: 'en', name: 'Sweet potato', kind: 'primary' }]),
  node(4, null, '菜品'),
  node(5, 4, '红薯'),
])

describe('sameNameCategories', () => {
  it('finds the categories that already carry a name, as their name or as an alias', () => {
    expect(sameNameCategories(tree, '红薯').map(match => [match.path, match.alias])).toEqual([
      ['食材 / 薯类 / 红薯', null],
      ['菜品 / 红薯', null],
    ])
    expect(sameNameCategories(tree, ' 番薯 ').map(match => [match.node.id, match.alias])).toEqual([[3, '番薯']])
    expect(sameNameCategories(tree, 'SWEET POTATO').map(match => match.alias)).toEqual(['Sweet potato'])
  })

  it('needs the whole name, and leaves out the category being edited', () => {
    expect(sameNameCategories(tree, '红')).toEqual([])
    expect(sameNameCategories(tree, '')).toEqual([])
    expect(sameNameCategories(tree, '红薯', 3).map(match => match.node.id)).toEqual([5])
  })
})
