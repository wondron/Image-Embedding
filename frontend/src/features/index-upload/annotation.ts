/**
 * What an upload carries besides the file, as the import page edits it. Text and food rules are
 * the image's (entities/image/fields, repeating the backend's); this module adds the category,
 * the label status, the scene choices, source and license.
 */
import type { CategoryTree } from '../../entities/category'
import {
  AMOUNT_FIELDS,
  EMPTY_FOOD_FIELDS,
  foodFieldsFilled,
  imageFieldErrors,
  parseAmount,
  type AmountField,
  type FoodFieldValues,
  type ImageFieldName,
  type IndexOptions,
} from '../../entities/image'

export type SceneOverride = 'include' | 'exclude'
export type ImportLabelStatus = 'auto' | 'verified'

/**
 * One annotation. The batch has one; an image annotated on its own carries a full copy, so
 * later batch edits do not reach it. Amount fields keep the typed text.
 */
export interface ImportAnnotation extends FoodFieldValues {
  categoryId: number | null
  /** Sent only with a category (unlabeled ⟺ no category). */
  labelStatus: ImportLabelStatus
  /** By scene code: where the person went against the scene rules (scenePlan). */
  sceneOverrides: Readonly<Record<string, SceneOverride>>
  tags: readonly string[]
  description: string
  /** Editable enum values (GET /api/enums). */
  sourceType: string
  license: string
}

// backend/app/core/enums.py: the values the server uses when none is sent; always in the enum file
export const DEFAULT_SOURCE_TYPE = 'user_upload'
export const DEFAULT_LICENSE = 'unknown'

export type AnnotationField = 'category' | ImageFieldName
export type AnnotationErrors = Partial<Record<AnnotationField, string>>

export function emptyAnnotation(): ImportAnnotation {
  return {
    categoryId: null,
    labelStatus: 'auto',
    sceneOverrides: {},
    tags: [],
    description: '',
    ...EMPTY_FOOD_FIELDS,
    sourceType: DEFAULT_SOURCE_TYPE,
    license: DEFAULT_LICENSE,
  }
}

/**
 * Field problems; empty when the annotation can be sent. A category needs the loaded tree: its
 * code is what the request carries, and a deleted or disabled category is refused here.
 */
export function validateAnnotation(annotation: ImportAnnotation, tree: CategoryTree | null): AnnotationErrors {
  const errors: AnnotationErrors = { ...imageFieldErrors(annotation) }
  if (annotation.categoryId !== null) {
    const node = tree?.byId.get(annotation.categoryId)?.node
    if (!tree) errors.category = '类别目录未加载，暂时不能提交所选类别。'
    else if (!node) errors.category = '所选类别已不存在，请重新选择。'
    else if (!node.isActive) errors.category = '所选类别已停用，请换一个类别。'
  }
  return errors
}

export function hasErrors(errors: Readonly<Record<string, string | undefined>>): boolean {
  return Object.keys(errors).length > 0
}

/** The request fields of an annotation for indexImage; the category goes by its code. */
export type IndexFields = Omit<IndexOptions, 'file' | 'files' | 'scenes' | 'baseUrl' | 'signal'>

/** Call after validateAnnotation passed: an invalid amount would be dropped here. */
export function toIndexFields(annotation: ImportAnnotation, tree: CategoryTree | null): IndexFields {
  const code = annotation.categoryId === null ? undefined : tree?.byId.get(annotation.categoryId)?.node.code
  const amount = (key: AmountField) => parseAmount(annotation[key], AMOUNT_FIELDS.find(field => field.key === key)!.min) ?? null
  return {
    category: code,
    labelStatus: code ? annotation.labelStatus : undefined,
    tags: annotation.tags.length ? [...annotation.tags] : undefined,
    description: annotation.description.trim() || undefined,
    sourceType: annotation.sourceType.trim() || undefined,
    license: annotation.license.trim() || undefined,
    foodState: annotation.foodState || null,
    foodProcessing: annotation.foodProcessing || null,
    foodCount: amount('foodCount'),
    totalMassG: amount('totalMassG'),
    waterMassG: amount('waterMassG'),
    mainIngredientMassG: amount('mainIngredientMassG'),
  }
}

/** How many optional fields (tags, description, food) are filled, for a compact summary. */
export function filledExtras(annotation: ImportAnnotation): number {
  return (annotation.tags.length ? 1 : 0) + (annotation.description.trim() ? 1 : 0) + foodFieldsFilled(annotation)
}
