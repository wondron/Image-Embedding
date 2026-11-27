import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import type { ImageDetail } from '../../entities/image'
import type { Scene } from '../../entities/scene'
import type { SearchImage } from '../../entities/search-result'
import {
  NO_RESULT_EDITS,
  applyResultEdits,
  isEditableResult,
  savedAnnotation,
  savedSceneLabel,
  sceneLabelFor,
  withDeleted,
  withSaved,
} from './resultEdits'

function node(id: number, parentId: number | null, name: string): CategoryNode {
  return {
    id, code: `c${id}`, parentId, path: String(id), name, names: [], cuisines: [], heatingGroup: null,
    sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
  }
}

// 菜品 › 肉类 › (牛排, 红烧肉); 菜品 › 蔬菜
const tree = buildCategoryTree([node(1, null, '菜品'), node(2, 1, '肉类'), node(3, 2, '牛排'), node(4, 2, '红烧肉'), node(5, 1, '蔬菜')])

const ref = (id: number, name: string) => ({ id, code: `x${id}`, name })
const oven: Pick<Scene, 'labels' | 'rules'> = {
  labels: [
    { id: 10, code: 'meat', name: '烤肉', sortOrder: 0, isActive: true, ruleCount: 1 },
    { id: 11, code: 'steak', name: '牛排档', sortOrder: 1, isActive: true, ruleCount: 1 },
    { id: 12, code: 'veg', name: '烤蔬菜', sortOrder: 2, isActive: false, ruleCount: 1 },
  ],
  rules: [
    { id: 1, category: ref(2, '肉类'), cuisine: null, label: ref(10, '烤肉') },
    { id: 2, category: ref(3, '牛排'), cuisine: null, label: ref(11, '牛排档') },
    { id: 3, category: ref(5, '蔬菜'), cuisine: null, label: ref(12, '烤蔬菜') },
    { id: 4, category: null, cuisine: '川菜', label: null },
  ],
}

const ID = '00000000-0000-4000-8000-00000000000a'

function card(overrides: Partial<SearchImage> = {}): SearchImage {
  return {
    id: ID, title: '牛排档', tags: '带骨', imageUrl: null, thumbnailUrl: null, alternateUrls: [], similarity: 0.82,
    categoryId: 3, category: '牛排', scenes: ['general', 'oven'], sceneLabel: '牛排档', labelStatus: 'auto',
    description: '煎牛排', foodState: 'raw', heatingGroup: null, width: 640, height: 480,
    outputsHeating: true, source: 'self', pageUrl: null, siteName: null, ...overrides,
  }
}

function image(overrides: Partial<ImageDetail> = {}): ImageDetail {
  return {
    id: ID, sha256: null, imageUrl: null, originalUrl: null, width: 640, height: 480,
    categoryId: 3, category: '牛排', categoryCode: 'c3', labelStatus: 'auto', scenes: ['general', 'oven'],
    description: '煎牛排', tags: ['带骨'], sourceType: null, license: null,
    foodState: 'raw', foodProcessing: null, foodCount: null, totalMassG: null, waterMassG: null, mainIngredientMassG: null,
    heatingGroup: null, payloadVersion: 1, createdAt: null, updatedAt: null, embeddings: [], ...overrides,
  }
}

describe('sceneLabelFor', () => {
  it('takes the deepest rule on the category or an ancestor that maps to an active label', () => {
    expect(sceneLabelFor(oven, 3, tree)).toBe('牛排档')  // its own rule beats its parent's
    expect(sceneLabelFor(oven, 4, tree)).toBe('烤肉')    // inherited from 肉类
  })

  it('is null without such a rule, for an inactive label, and for a category the tree does not have', () => {
    expect(sceneLabelFor(oven, 1, tree)).toBeNull()
    expect(sceneLabelFor(oven, 5, tree)).toBeNull()
    expect(sceneLabelFor(oven, 99, tree)).toBeNull()
    expect(sceneLabelFor({ labels: [], rules: [] }, 3, tree)).toBeNull()
  })
})

describe('savedSceneLabel', () => {
  it("keeps the server's label while the category stays, works it out for another", () => {
    expect(savedSceneLabel(card({ sceneLabel: '服务端标签' }), image(), oven, tree)).toBe('服务端标签')
    expect(savedSceneLabel(card(), image({ categoryId: 4 }), oven, tree)).toBe('烤肉')
  })

  it('is null without a category, or without the scene or the tree', () => {
    expect(savedSceneLabel(card(), image({ categoryId: null }), oven, tree)).toBeNull()
    expect(savedSceneLabel(card(), image({ categoryId: 4 }), undefined, tree)).toBeNull()
    expect(savedSceneLabel(card(), image({ categoryId: 4 }), oven, null)).toBeNull()
  })
})

describe('applyResultEdits', () => {
  it('leaves the results alone when nothing was saved', () => {
    const items = [card()]
    const result = applyResultEdits(items, NO_RESULT_EDITS)
    expect(result.items).toEqual(items)
    expect(result.edited.size).toBe(0)
  })

  it('shows the saved annotation on the card, keeps the search score, and marks it edited', () => {
    const saved = savedAnnotation(image({ categoryId: 4, category: '红烧肉', labelStatus: 'verified', tags: ['红烧肉', '五花'], description: '' }), '烤肉')
    const other = card({ id: '00000000-0000-4000-8000-00000000000b', similarity: 0.5 })
    const result = applyResultEdits([card(), other], withSaved(NO_RESULT_EDITS, ID, saved))
    expect(result.items[0]).toMatchObject({
      categoryId: 4, category: '红烧肉', labelStatus: 'verified', tags: '红烧肉、五花', description: '',
      sceneLabel: '烤肉', title: '烤肉', similarity: 0.82,
    })
    expect(result.items[1]).toBe(other)
    expect([...result.edited]).toEqual([ID])
  })

  it('does not mark a card whose saved annotation is what the search returned', () => {
    const result = applyResultEdits([card()], withSaved(NO_RESULT_EDITS, ID, savedAnnotation(image({ scenes: ['oven', 'general'] }), '牛排档')))
    expect(result.edited.size).toBe(0)
  })

  it('takes the heating fields only for cards of scenes that output them', () => {
    const saved = savedAnnotation(image({ foodState: 'cooked', heatingGroup: { id: 3, code: 'roast', name: '烘烤' } }), '牛排档')
    const edits = withSaved(NO_RESULT_EDITS, ID, saved)
    expect(applyResultEdits([card()], edits).items[0]).toMatchObject({ foodState: 'cooked', heatingGroup: { id: 3 } })
    const plain = card({ outputsHeating: false, foodState: null })
    const result = applyResultEdits([plain], edits)
    expect(result.items[0]).toMatchObject({ foodState: null, heatingGroup: null })
    expect(result.edited.size).toBe(0)
  })

  it('names an unlabelled card by its tags, as the search does', () => {
    const saved = savedAnnotation(image({ categoryId: null, category: null, labelStatus: 'unlabeled', tags: ['待分类'] }), null)
    expect(applyResultEdits([card()], withSaved(NO_RESULT_EDITS, ID, saved)).items[0]).toMatchObject({ title: '待分类', sceneLabel: null, category: null })
  })

  it('leaves deleted images out, and touches only self-built hits', () => {
    const global = card({ id: ID, source: 'global' })
    const result = applyResultEdits([card(), global], withDeleted(NO_RESULT_EDITS, ID))
    expect(result.items).toEqual([global])
  })
})

describe('isEditableResult', () => {
  it('is a self-built hit with the stored image id', () => {
    expect(isEditableResult({ id: ID, source: 'self' })).toBe(true)
    expect(isEditableResult({ id: 'image-1', source: 'self' })).toBe(false)
    expect(isEditableResult({ id: ID, source: 'global' })).toBe(false)
    expect(isEditableResult({ id: 'recipe-1', source: 'recipe' })).toBe(false)
  })
})
