/**
 * What an upload carries besides the file, as the import page edits it. The checks repeat
 * backend/app/services/retrieval/{text_fields,food_fields}.py so a mistake shows next to its field
 * before anything is sent; the server checks everything again.
 */
import type { CategoryTree } from '../../entities/category'
import type { IndexOptions } from '../../entities/image'

export type SceneOverride = 'include' | 'exclude'
export type ImportLabelStatus = 'auto' | 'verified'

/**
 * One annotation. The batch has one; an image annotated on its own carries a full copy, so
 * later batch edits do not reach it. Amount fields keep the typed text.
 */
export interface ImportAnnotation {
  categoryId: number | null
  /** Sent only with a category (unlabeled ⟺ no category). */
  labelStatus: ImportLabelStatus
  /** By scene code: where the person went against the scene rules (scenePlan). */
  sceneOverrides: Readonly<Record<string, SceneOverride>>
  tags: readonly string[]
  description: string
  /** Editable enum values; '' = not annotated. */
  foodState: string
  foodProcessing: string
  foodCount: string
  totalMassG: string
  waterMassG: string
  mainIngredientMassG: string
  /** Editable enum values (GET /api/enums). */
  sourceType: string
  license: string
}

// backend/app/core/enums.py: the values the server uses when none is sent; always in the enum file
export const DEFAULT_SOURCE_TYPE = 'user_upload'
export const DEFAULT_LICENSE = 'unknown'
export const FOOD_STATE_ENUM = 'food_state'
export const FOOD_PROCESSING_ENUM = 'food_processing'
export const SOURCE_TYPE_ENUM = 'source_type'
export const LICENSE_ENUM = 'license'
// backend/app/services/retrieval/text_fields.py
export const MAX_DESCRIPTION_LENGTH = 1000
export const MAX_TAGS = 32
export const MAX_TAG_LENGTH = 64
const PG_INT_MAX = 2_147_483_647

/** The amount fields: whole numbers, at least `min` (the CHECKs on image_foods say the same). */
export const AMOUNT_FIELDS = [
  { key: 'foodCount', label: '个数', unit: '', min: 1 },
  { key: 'totalMassG', label: '总质量', unit: 'g', min: 1 },
  { key: 'waterMassG', label: '含水质量', unit: 'g', min: 0 },
  { key: 'mainIngredientMassG', label: '主食材质量', unit: 'g', min: 1 },
] as const
export type AmountField = (typeof AMOUNT_FIELDS)[number]['key']

export type AnnotationField = 'category' | 'description' | 'tags' | AmountField
export type AnnotationErrors = Partial<Record<AnnotationField, string>>

export function emptyAnnotation(): ImportAnnotation {
  return {
    categoryId: null,
    labelStatus: 'auto',
    sceneOverrides: {},
    tags: [],
    description: '',
    foodState: '',
    foodProcessing: '',
    foodCount: '',
    totalMassG: '',
    waterMassG: '',
    mainIngredientMassG: '',
    sourceType: DEFAULT_SOURCE_TYPE,
    license: DEFAULT_LICENSE,
  }
}

export function charLength(text: string): number {
  return Array.from(text).length
}

/** A whole number >= min from typed text: null when blank, undefined when it is not one. */
export function parseAmount(text: string, min: number): number | null | undefined {
  const trimmed = text.trim()
  if (!trimmed) return null
  if (!/^\d+$/.test(trimmed)) return undefined
  const value = Number(trimmed)
  return Number.isSafeInteger(value) && value >= min && value <= PG_INT_MAX ? value : undefined
}

/**
 * Field problems; empty when the annotation can be sent. A category needs the loaded tree: its
 * code is what the request carries, and a deleted or disabled category is refused here.
 */
export function validateAnnotation(annotation: ImportAnnotation, tree: CategoryTree | null): AnnotationErrors {
  const errors: AnnotationErrors = {}
  if (annotation.categoryId !== null) {
    const node = tree?.byId.get(annotation.categoryId)?.node
    if (!tree) errors.category = '类别目录未加载，暂时不能提交所选类别。'
    else if (!node) errors.category = '所选类别已不存在，请重新选择。'
    else if (!node.isActive) errors.category = '所选类别已停用，请换一个类别。'
  }
  if (charLength(annotation.description.trim()) > MAX_DESCRIPTION_LENGTH) {
    errors.description = `描述最多 ${MAX_DESCRIPTION_LENGTH} 个字符。`
  }
  if (annotation.tags.length > MAX_TAGS) errors.tags = `最多 ${MAX_TAGS} 个标签。`
  else if (annotation.tags.some(tag => charLength(tag) > MAX_TAG_LENGTH)) errors.tags = `每个标签最多 ${MAX_TAG_LENGTH} 个字符。`
  for (const field of AMOUNT_FIELDS) {
    if (parseAmount(annotation[field.key], field.min) === undefined) {
      errors[field.key] = field.min === 0 ? '请输入 0 或正整数。' : '请输入正整数。'
    }
  }
  return errors
}

export function hasErrors(errors: AnnotationErrors): boolean {
  return Object.keys(errors).length > 0
}

/** The request fields of an annotation for indexImage; the category goes by its code. */
export type IndexFields = Omit<IndexOptions, 'file' | 'files' | 'scenes' | 'baseUrl' | 'signal'>

/** Call after validateAnnotation passed: an invalid amount would be dropped here. */
export function toIndexFields(annotation: ImportAnnotation, tree: CategoryTree | null): IndexFields {
  const code = annotation.categoryId === null ? undefined : tree?.byId.get(annotation.categoryId)?.node.code
  const amount = (key: AmountField, min: number) => parseAmount(annotation[key], min) ?? null
  return {
    category: code,
    labelStatus: code ? annotation.labelStatus : undefined,
    tags: annotation.tags.length ? [...annotation.tags] : undefined,
    description: annotation.description.trim() || undefined,
    sourceType: annotation.sourceType.trim() || undefined,
    license: annotation.license.trim() || undefined,
    foodState: annotation.foodState || null,
    foodProcessing: annotation.foodProcessing || null,
    foodCount: amount('foodCount', 1),
    totalMassG: amount('totalMassG', 1),
    waterMassG: amount('waterMassG', 0),
    mainIngredientMassG: amount('mainIngredientMassG', 1),
  }
}

/** How many optional fields (tags, description, food) are filled, for a compact summary. */
export function filledExtras(annotation: ImportAnnotation): number {
  let count = annotation.tags.length ? 1 : 0
  if (annotation.description.trim()) count++
  if (annotation.foodState) count++
  if (annotation.foodProcessing) count++
  for (const field of AMOUNT_FIELDS) if (annotation[field.key].trim()) count++
  return count
}
