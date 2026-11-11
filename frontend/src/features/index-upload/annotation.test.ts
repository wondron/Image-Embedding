import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { emptyAnnotation, filledExtras, parseAmount, toIndexFields, validateAnnotation, type ImportAnnotation } from './annotation'

function node(id: number, code: string, extra: Partial<CategoryNode> = {}): CategoryNode {
  return {
    id, code, parentId: null, path: String(id), name: code, names: [], cuisines: [], heatingGroup: null,
    sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null, ...extra,
  }
}

const tree = buildCategoryTree([node(1, 'dish'), node(2, 'dish.old', { isActive: false })])
const annotation = (patch: Partial<ImportAnnotation>): ImportAnnotation => ({ ...emptyAnnotation(), ...patch })

describe('parseAmount', () => {
  it('reads whole numbers at or above the minimum', () => {
    expect(parseAmount(' 12 ', 1)).toBe(12)
    expect(parseAmount('0', 0)).toBe(0)
    expect(parseAmount('', 1)).toBeNull()
    for (const bad of ['0', '-1', '1.5', '1e3', 'abc', '2147483648']) expect(parseAmount(bad, 1)).toBeUndefined()
  })
})

describe('validateAnnotation', () => {
  it('accepts the empty annotation', () => {
    expect(validateAnnotation(emptyAnnotation(), null)).toEqual({})
  })

  it('names each field with a problem', () => {
    const errors = validateAnnotation(annotation({
      categoryId: 2, description: 'x'.repeat(1001), foodCount: '0', waterMassG: '-1', totalMassG: '3.5',
    }), tree)
    expect(Object.keys(errors).sort()).toEqual(['category', 'description', 'foodCount', 'totalMassG', 'waterMassG'])
    expect(errors.category).toContain('停用')
    expect(validateAnnotation(annotation({ categoryId: 9 }), tree).category).toContain('不存在')
    expect(validateAnnotation(annotation({ categoryId: 1 }), null).category).toContain('未加载')  // its code is unknown
    expect(validateAnnotation(annotation({ tags: Array.from({ length: 33 }, (_, i) => `t${i}`) }), tree).tags).toContain('32')
  })
})

describe('toIndexFields', () => {
  it('sends the category by code with its label status, and parsed amounts', () => {
    expect(toIndexFields(annotation({
      categoryId: 1, labelStatus: 'verified', tags: ['带皮'], description: '  整块 ', foodState: 'raw',
      foodCount: '3', waterMassG: '0',
    }), tree)).toEqual({
      category: 'dish', labelStatus: 'verified', tags: ['带皮'], description: '整块',
      sourceType: 'user_upload', license: 'unknown',
      foodState: 'raw', foodProcessing: null, foodCount: 3, totalMassG: null, waterMassG: 0, mainIngredientMassG: null,
    })
  })

  it('leaves the label status out without a category', () => {
    const fields = toIndexFields(annotation({ labelStatus: 'verified' }), tree)
    expect(fields.category).toBeUndefined()
    expect(fields.labelStatus).toBeUndefined()
    expect(fields.tags).toBeUndefined()
    expect(fields.description).toBeUndefined()
  })

  it('counts the optional fields that are filled', () => {
    expect(filledExtras(emptyAnnotation())).toBe(0)
    expect(filledExtras(annotation({ tags: ['a', 'b'], description: 'x', foodState: 'raw', totalMassG: '5' }))).toBe(4)
  })
})
