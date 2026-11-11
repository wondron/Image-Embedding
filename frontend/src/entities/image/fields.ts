/**
 * Field rules for an image's free text and food annotation, as the backend checks them
 * (backend/app/services/retrieval/text_fields.py and food_fields.py). Forms show a mistake next to
 * its field before anything is sent; the server checks everything again.
 */

export const MAX_DESCRIPTION_LENGTH = 1000
export const MAX_TAGS = 32
export const MAX_TAG_LENGTH = 64
const PG_INT_MAX = 2_147_483_647

// Editable enums (GET /api/enums); food_state is FOOD_STATE_ENUM in ./model
export const FOOD_PROCESSING_ENUM = 'food_processing'
export const SOURCE_TYPE_ENUM = 'source_type'
export const LICENSE_ENUM = 'license'

/** The amount fields: whole numbers, at least `min` (the CHECKs on image_foods say the same). */
export const AMOUNT_FIELDS = [
  { key: 'foodCount', wire: 'food_count', label: '个数', unit: '', min: 1 },
  { key: 'totalMassG', wire: 'total_mass_g', label: '总质量', unit: 'g', min: 1 },
  { key: 'waterMassG', wire: 'water_mass_g', label: '含水质量', unit: 'g', min: 0 },
  { key: 'mainIngredientMassG', wire: 'main_ingredient_mass_g', label: '主食材质量', unit: 'g', min: 1 },
] as const
export type AmountField = (typeof AMOUNT_FIELDS)[number]['key']

/** The food annotation as a form edits it: enum values ('' = not annotated), amounts as typed. */
export interface FoodFieldValues {
  foodState: string
  foodProcessing: string
  foodCount: string
  totalMassG: string
  waterMassG: string
  mainIngredientMassG: string
}

export const EMPTY_FOOD_FIELDS: FoodFieldValues = {
  foodState: '',
  foodProcessing: '',
  foodCount: '',
  totalMassG: '',
  waterMassG: '',
  mainIngredientMassG: '',
}

export interface TextFieldValues {
  description: string
  tags: readonly string[]
}

export type ImageFieldName = 'description' | 'tags' | AmountField
export type ImageFieldErrors = Partial<Record<ImageFieldName, string>>

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

/** Problems with the description, the tags and the amounts; empty when they can be sent. */
export function imageFieldErrors(values: TextFieldValues & FoodFieldValues): ImageFieldErrors {
  const errors: ImageFieldErrors = {}
  if (charLength(values.description.trim()) > MAX_DESCRIPTION_LENGTH) {
    errors.description = `描述最多 ${MAX_DESCRIPTION_LENGTH} 个字符。`
  }
  if (values.tags.length > MAX_TAGS) errors.tags = `最多 ${MAX_TAGS} 个标签。`
  else if (values.tags.some(tag => charLength(tag) > MAX_TAG_LENGTH)) errors.tags = `每个标签最多 ${MAX_TAG_LENGTH} 个字符。`
  for (const field of AMOUNT_FIELDS) {
    if (parseAmount(values[field.key], field.min) === undefined) {
      errors[field.key] = field.min === 0 ? '请输入 0 或正整数。' : '请输入正整数。'
    }
  }
  return errors
}

/** How many food fields are filled. */
export function foodFieldsFilled(values: FoodFieldValues): number {
  return [values.foodState, values.foodProcessing, ...AMOUNT_FIELDS.map(field => values[field.key].trim())].filter(Boolean).length
}
