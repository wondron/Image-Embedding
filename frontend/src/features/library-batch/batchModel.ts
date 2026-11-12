/**
 * Changing the annotation of many library images at once. Every field starts at 「不修改」: only
 * what the person set is sent, image by image (PATCH /api/images/{id}), with the rules of
 * backend app/services/retrieval/image_fields.py — clearing the category makes an image unlabeled,
 * a category set without a label status is verified, an explicit label status must agree with the
 * category (so images without one are skipped for it).
 */
import type { CategoryTree } from '../../entities/category'
import {
  AMOUNT_FIELDS,
  MAX_DESCRIPTION_LENGTH,
  MAX_TAGS,
  MAX_TAG_LENGTH,
  charLength,
  parseAmount,
  type AmountField,
  type ImagePatch,
} from '../../entities/image'

/** keep: 不修改; set: 改为 the value; clear: 清空. */
export type FieldMode = 'keep' | 'set' | 'clear'
export type TagMode = 'keep' | 'add' | 'remove' | 'replace' | 'clear'
export type BatchLabelStatus = 'keep' | 'auto' | 'verified'

export interface BatchEdit {
  category: { mode: FieldMode; categoryId: number | null }
  labelStatus: BatchLabelStatus
  tags: { mode: TagMode; values: readonly string[] }
  description: { mode: FieldMode; text: string }
  foodState: { mode: FieldMode; value: string }
  foodProcessing: { mode: FieldMode; value: string }
  /** The amounts as typed. */
  amounts: Readonly<Record<AmountField, { mode: FieldMode; text: string }>>
}

export const EMPTY_BATCH_EDIT: BatchEdit = {
  category: { mode: 'keep', categoryId: null },
  labelStatus: 'keep',
  tags: { mode: 'keep', values: [] },
  description: { mode: 'keep', text: '' },
  foodState: { mode: 'keep', value: '' },
  foodProcessing: { mode: 'keep', value: '' },
  amounts: Object.fromEntries(AMOUNT_FIELDS.map(field => [field.key, { mode: 'keep', text: '' }])) as BatchEdit['amounts'],
}

export type BatchEditField = 'category' | 'labelStatus' | 'tags' | 'description' | 'foodState' | 'foodProcessing' | AmountField
export type BatchEditErrors = Partial<Record<BatchEditField, string>>

/** What a field is called in the summary and the messages. */
export const BATCH_FIELD_LABEL: Readonly<Record<BatchEditField, string>> = {
  category: '类别',
  labelStatus: '标注状态',
  tags: '标签',
  description: '描述',
  foodState: '食物状态',
  foodProcessing: '加工状态',
  ...Object.fromEntries(AMOUNT_FIELDS.map(field => [field.key, field.label])) as Record<AmountField, string>,
}

/** The fields the edit changes, in form order. */
export function changedFields(edit: BatchEdit): BatchEditField[] {
  const fields: BatchEditField[] = []
  if (edit.category.mode !== 'keep') fields.push('category')
  if (edit.labelStatus !== 'keep') fields.push('labelStatus')
  if (edit.tags.mode !== 'keep') fields.push('tags')
  if (edit.description.mode !== 'keep') fields.push('description')
  if (edit.foodState.mode !== 'keep') fields.push('foodState')
  if (edit.foodProcessing.mode !== 'keep') fields.push('foodProcessing')
  for (const field of AMOUNT_FIELDS) if (edit.amounts[field.key].mode !== 'keep') fields.push(field.key)
  return fields
}

/** 「类别、标签」 */
export function describeBatchEdit(edit: BatchEdit): string {
  return changedFields(edit).map(field => BATCH_FIELD_LABEL[field]).join('、')
}

/** Problems with the edit; empty when it can run (an edit that changes nothing is not an error here). */
export function validateBatchEdit(edit: BatchEdit, tree: CategoryTree | null): BatchEditErrors {
  const errors: BatchEditErrors = {}
  if (edit.category.mode === 'set') {
    const node = edit.category.categoryId === null ? null : tree?.byId.get(edit.category.categoryId)?.node
    if (edit.category.categoryId === null) errors.category = '请选择新的类别；要变成未标注请选「清空」。'
    else if (!tree) errors.category = '类别目录未加载，暂时不能更换类别。'
    else if (!node) errors.category = '所选类别已不存在，请重新选择。'
    else if (!node.isActive) errors.category = '所选类别已停用，请换一个类别。'
  }
  if (edit.labelStatus !== 'keep' && edit.category.mode === 'clear') {
    errors.labelStatus = '清空类别后图片就是未标注，不能再设标注状态。'
  }
  const { mode, values } = edit.tags
  if (mode === 'add' || mode === 'remove' || mode === 'replace') {
    if (!values.length) errors.tags = mode === 'replace' ? '请输入标签；要去掉全部标签请选「清空」。' : '请输入至少一个标签。'
    else if (values.some(tag => charLength(tag) > MAX_TAG_LENGTH)) errors.tags = `每个标签最多 ${MAX_TAG_LENGTH} 个字符。`
    else if (values.length > MAX_TAGS) errors.tags = `最多 ${MAX_TAGS} 个标签。`
  }
  if (edit.description.mode === 'set') {
    if (!edit.description.text.trim()) errors.description = '请输入描述；要清空请选「清空」。'
    else if (charLength(edit.description.text.trim()) > MAX_DESCRIPTION_LENGTH) errors.description = `描述最多 ${MAX_DESCRIPTION_LENGTH} 个字符。`
  }
  if (edit.foodState.mode === 'set' && !edit.foodState.value) errors.foodState = '请选择食物状态。'
  if (edit.foodProcessing.mode === 'set' && !edit.foodProcessing.value) errors.foodProcessing = '请选择加工状态。'
  for (const field of AMOUNT_FIELDS) {
    const { mode: amountMode, text } = edit.amounts[field.key]
    const value = parseAmount(text, field.min)
    if (amountMode === 'set' && (value === null || value === undefined)) {
      errors[field.key] = !text.trim() ? '请输入数值；要清空请选「清空」。' : field.min === 0 ? '请输入 0 或正整数。' : '请输入正整数。'
    }
  }
  return errors
}

/** Adding or removing tags works on each image's tags as they are now: read them first. */
export function needsCurrentTags(edit: BatchEdit): boolean {
  return edit.tags.mode === 'add' || edit.tags.mode === 'remove'
}

/** One image as the plan sees it now. */
export interface ImageState {
  categoryId: number | null
  tags: readonly string[]
}

export type ImagePlan =
  | { kind: 'patch'; patch: ImagePatch; note?: string }
  | { kind: 'skip'; reason: string }
  | { kind: 'fail'; reason: string }

const NO_CATEGORY_NOTE = '没有类别，标注状态没改'

/** What to send for one image (call after validateBatchEdit passed). */
export function planImagePatch(edit: BatchEdit, image: ImageState): ImagePlan {
  const patch: ImagePatch = {}
  let note: string | undefined
  let category = image.categoryId
  if (edit.category.mode === 'set') {
    patch.category_id = edit.category.categoryId
    category = edit.category.categoryId
  } else if (edit.category.mode === 'clear') {
    patch.category_id = null
    category = null
  }
  if (edit.labelStatus !== 'keep') {
    // unlabeled ⟺ no category: an image without one keeps its status
    if (category !== null) patch.label_status = edit.labelStatus
    else note = NO_CATEGORY_NOTE
  }

  const tags = image.tags
  let nextTags: readonly string[] | null = null
  if (edit.tags.mode === 'add') nextTags = [...new Set([...tags, ...edit.tags.values])]
  else if (edit.tags.mode === 'remove') nextTags = tags.filter(tag => !edit.tags.values.includes(tag))
  else if (edit.tags.mode === 'replace') nextTags = [...new Set(edit.tags.values)]
  else if (edit.tags.mode === 'clear') nextTags = []
  if (nextTags) {
    if (nextTags.length > MAX_TAGS) return { kind: 'fail', reason: `加上后有 ${nextTags.length} 个标签，最多 ${MAX_TAGS} 个` }
    // Tags that would stay as they are are not sent
    const same = nextTags.length === tags.length && nextTags.every((tag, index) => tag === tags[index])
    if (!same) patch.tags = [...nextTags]
  }

  if (edit.description.mode === 'set') patch.description = edit.description.text.trim()
  else if (edit.description.mode === 'clear') patch.description = null
  if (edit.foodState.mode !== 'keep') patch.food_state = edit.foodState.mode === 'set' ? edit.foodState.value : null
  if (edit.foodProcessing.mode !== 'keep') patch.food_processing = edit.foodProcessing.mode === 'set' ? edit.foodProcessing.value : null
  for (const field of AMOUNT_FIELDS) {
    const amount = edit.amounts[field.key]
    if (amount.mode !== 'keep') patch[field.wire] = amount.mode === 'set' ? parseAmount(amount.text, field.min) ?? null : null
  }

  if (!Object.keys(patch).length) {
    return { kind: 'skip', reason: note === NO_CATEGORY_NOTE ? '没有类别，不能设标注状态' : '已经是这样了，不用改' }
  }
  return note ? { kind: 'patch', patch, note } : { kind: 'patch', patch }
}
