import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import type { ImageDetail } from '../../entities/image'
import { describePatch, draftFromImage, isDirty, toImagePatch, validateDraft, withCategory } from './editDraft'

function node(id: number, code: string, isActive = true): CategoryNode {
  return {
    id, code, parentId: null, path: String(id), name: code, names: [], cuisines: [], heatingGroup: null,
    sortOrder: 0, isActive, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
  }
}

const tree = buildCategoryTree([node(1, 'dish.steak'), node(2, 'dish.pork'), node(3, 'dish.old', false)])

const IMAGE: ImageDetail = {
  id: 'img-1', sha256: 'ab', imageUrl: null, originalUrl: null, width: 640, height: 480,
  categoryId: 1, category: '牛排', categoryCode: 'dish.steak', labelStatus: 'auto', scenes: ['general'],
  description: '煎牛排', tags: ['带骨'], sourceType: 'user_upload', license: 'unknown',
  foodState: 'raw', foodProcessing: null, foodCount: 2, totalMassG: null, waterMassG: 0, mainIngredientMassG: null,
  heatingGroup: null, payloadVersion: 3, createdAt: null, updatedAt: null, embeddings: [],
}

describe('draftFromImage / toImagePatch', () => {
  it('starts from what is stored and sends nothing untouched', () => {
    const draft = draftFromImage(IMAGE)
    expect(draft).toMatchObject({ categoryId: 1, labelStatus: 'auto', foodCount: '2', waterMassG: '0', totalMassG: '' })
    expect(toImagePatch(IMAGE, draft)).toEqual({})
    expect(isDirty(IMAGE, draft)).toBe(false)
  })

  it('sends only the fields that changed, null to clear', () => {
    const draft = { ...draftFromImage(IMAGE), tags: ['带骨', '五分熟'], description: ' 煎牛排 ', foodState: '', totalMassG: '350', foodCount: '' }
    expect(toImagePatch(IMAGE, draft)).toEqual({ tags: ['带骨', '五分熟'], food_state: null, total_mass_g: 350, food_count: null })
    expect(describePatch(toImagePatch(IMAGE, draft))).toBe('标签、食物状态、个数、总质量')
  })

  it('makes a category picked by hand verified, and says so explicitly', () => {
    const draft = withCategory(draftFromImage(IMAGE), IMAGE, 2)
    expect(draft.labelStatus).toBe('verified')
    expect(toImagePatch(IMAGE, draft)).toEqual({ category_id: 2, label_status: 'verified' })
    // back to the stored category: its stored label comes back, nothing to send
    expect(toImagePatch(IMAGE, withCategory(draft, IMAGE, 1))).toEqual({})
    // auto kept by choice
    expect(toImagePatch(IMAGE, { ...draft, labelStatus: 'auto' })).toEqual({ category_id: 2, label_status: 'auto' })
  })

  it('clears the category without a label (the server makes the image unlabeled)', () => {
    expect(toImagePatch(IMAGE, withCategory(draftFromImage(IMAGE), IMAGE, null))).toEqual({ category_id: null })
    const unlabeled = { ...IMAGE, categoryId: null, labelStatus: 'unlabeled' as const }
    expect(toImagePatch(unlabeled, withCategory(draftFromImage(unlabeled), unlabeled, 2))).toEqual({ category_id: 2, label_status: 'verified' })
  })

  it('only changes the label status of the same category', () => {
    expect(toImagePatch(IMAGE, { ...draftFromImage(IMAGE), labelStatus: 'verified' })).toEqual({ label_status: 'verified' })
  })
})

describe('validateDraft / isDirty', () => {
  it('keeps a stored category that was disabled since, refuses picking one', () => {
    const disabled = { ...IMAGE, categoryId: 3 }
    expect(validateDraft(draftFromImage(disabled), disabled, tree)).toEqual({})
    expect(validateDraft(withCategory(draftFromImage(IMAGE), IMAGE, 3), IMAGE, tree).category).toContain('停用')
    expect(validateDraft(withCategory(draftFromImage(IMAGE), IMAGE, 9), IMAGE, tree).category).toContain('不存在')
    expect(validateDraft(withCategory(draftFromImage(IMAGE), IMAGE, 2), IMAGE, null).category).toContain('未加载')
  })

  it('checks amounts and counts a typing mistake as a change', () => {
    const draft = { ...draftFromImage(IMAGE), totalMassG: '3x' }
    expect(validateDraft(draft, IMAGE, tree)).toEqual({ totalMassG: '请输入正整数。' })
    expect(isDirty(IMAGE, draft)).toBe(true)
    expect(isDirty(IMAGE, { ...draftFromImage(IMAGE), description: '煎牛排  ' })).toBe(false)  // only spaces around
  })
})
