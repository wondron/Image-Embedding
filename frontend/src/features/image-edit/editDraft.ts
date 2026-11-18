/**
 * Editing one stored image (PATCH /api/images/{image_id}). The draft starts from the image's
 * detail; the patch carries only what changed, so a field the person did not touch is never
 * sent. The rules repeat backend app/services/retrieval/image_fields.py: a category picked by hand
 * is verified unless the person says auto, clearing the category makes the image unlabeled.
 */
import type { CategoryTree } from '../../entities/category'
import {
  AMOUNT_FIELDS,
  imageFieldErrors,
  parseAmount,
  renameTag,
  tagRename,
  type FoodFieldValues,
  type ImageDetail,
  type ImageFieldName,
  type ImagePatch,
  type TagRename,
} from '../../entities/image'

export type EditLabelStatus = 'auto' | 'verified'

/** The tag swap turned on, with the tags as they were before it. */
export interface TagSwap extends TagRename {
  before: readonly string[]
}

/** What the editor changes; amounts keep the typed text. */
export interface ImageEditDraft extends FoodFieldValues {
  categoryId: number | null
  /** With a category; without one the image is unlabeled. */
  labelStatus: EditLabelStatus
  tags: readonly string[]
  description: string
  /** The stored category's name in the tags became the picked category's name (see withTagSwap). */
  tagSwap: TagSwap | null
}

export type EditField = 'category' | ImageFieldName
export type EditErrors = Partial<Record<EditField, string>>

const amountText = (value: number | null) => (value === null ? '' : String(value))

export function draftFromImage(image: ImageDetail): ImageEditDraft {
  return {
    categoryId: image.categoryId,
    labelStatus: image.labelStatus === 'auto' ? 'auto' : 'verified',
    tags: [...image.tags],
    description: image.description,
    foodState: image.foodState ?? '',
    foodProcessing: image.foodProcessing ?? '',
    foodCount: amountText(image.foodCount),
    totalMassG: amountText(image.totalMassG),
    waterMassG: amountText(image.waterMassG),
    mainIngredientMassG: amountText(image.mainIngredientMassG),
    tagSwap: null,
  }
}

/**
 * The draft after picking a category: one picked by hand is verified (what the server would make
 * it); going back to the stored category brings its stored label back. A tag swap made for the
 * previous pick is undone (it named that category).
 */
export function withCategory(draft: ImageEditDraft, image: ImageDetail, categoryId: number | null): ImageEditDraft {
  const stored = categoryId !== null && categoryId === image.categoryId && image.labelStatus === 'auto'
  return { ...withTagSwap(draft, null), categoryId, labelStatus: stored ? 'auto' : 'verified' }
}

/**
 * The tag swap offered while the category changes: the stored category's name, when the tags have
 * it, can become the picked category's name. A swap that is on stays offered, so it can be turned
 * off again; null when there is nothing to offer.
 */
export function categoryTagOffer(image: ImageDetail, draft: ImageEditDraft, tree: CategoryTree | null): TagRename | null {
  if (draft.tagSwap) return { from: draft.tagSwap.from, to: draft.tagSwap.to }
  if (!tree || image.categoryId === null || draft.categoryId === null || draft.categoryId === image.categoryId) return null
  const rename = tagRename(tree.byId.get(image.categoryId)?.node.name, tree.byId.get(draft.categoryId)?.node.name)
  return rename && draft.tags.includes(rename.from) ? rename : null
}

/**
 * Turns the tag swap on (with the offered rename) or off (null), in the tags at once. Off puts the
 * tags back as they were; if they were edited meanwhile, the new name goes back to the old one.
 */
export function withTagSwap(draft: ImageEditDraft, rename: TagRename | null): ImageEditDraft {
  const swap = draft.tagSwap
  if (rename) return swap ? draft : { ...draft, tags: renameTag(draft.tags, rename), tagSwap: { ...rename, before: draft.tags } }
  if (!swap) return draft
  const untouched = sameList(draft.tags, renameTag(swap.before, swap))
  return { ...draft, tags: untouched ? swap.before : renameTag(draft.tags, { from: swap.to, to: swap.from }), tagSwap: null }
}

/**
 * Field problems; empty when the draft can be saved. Only a category the person picked is
 * checked against the tree: keeping a category that was disabled since is allowed.
 */
export function validateDraft(draft: ImageEditDraft, image: ImageDetail, tree: CategoryTree | null): EditErrors {
  const errors: EditErrors = { ...imageFieldErrors(draft) }
  if (draft.categoryId !== null && draft.categoryId !== image.categoryId) {
    const node = tree?.byId.get(draft.categoryId)?.node
    if (!tree) errors.category = '类别目录未加载，暂时不能更换类别。'
    else if (!node) errors.category = '所选类别已不存在，请重新选择。'
    else if (!node.isActive) errors.category = '所选类别已停用，请换一个类别。'
  }
  return errors
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index])
}

/** The PATCH body: only what differs from the stored image ({} when nothing does). Call after validateDraft passed. */
export function toImagePatch(image: ImageDetail, draft: ImageEditDraft): ImagePatch {
  const patch: ImagePatch = {}
  const categoryChanged = draft.categoryId !== image.categoryId
  if (categoryChanged) patch.category_id = draft.categoryId
  // With a category the label is always explicit when the category moved, so the server derives nothing
  if (draft.categoryId !== null && (categoryChanged || draft.labelStatus !== image.labelStatus)) patch.label_status = draft.labelStatus
  if (!sameList(draft.tags, image.tags)) patch.tags = [...draft.tags]
  if (draft.description.trim() !== image.description.trim()) patch.description = draft.description.trim()
  if ((draft.foodState || null) !== image.foodState) patch.food_state = draft.foodState || null
  if ((draft.foodProcessing || null) !== image.foodProcessing) patch.food_processing = draft.foodProcessing || null
  for (const field of AMOUNT_FIELDS) {
    const value = parseAmount(draft[field.key], field.min) ?? null
    if (value !== image[field.key]) patch[field.wire] = value
  }
  return patch
}

/** True when the draft differs from the stored image, typing mistakes included. */
export function isDirty(image: ImageDetail, draft: ImageEditDraft): boolean {
  const stored = draftFromImage(image)
  const text = (value: string) => value.trim()
  return draft.categoryId !== stored.categoryId
    || (draft.categoryId !== null && draft.labelStatus !== stored.labelStatus)
    || !sameList(draft.tags, stored.tags)
    || text(draft.description) !== text(stored.description)
    || draft.foodState !== stored.foodState
    || draft.foodProcessing !== stored.foodProcessing
    || AMOUNT_FIELDS.some(field => text(draft[field.key]) !== stored[field.key])
}

const PATCH_LABELS: Record<keyof ImagePatch, string> = {
  category_id: '类别',
  label_status: '标注状态',
  tags: '标签',
  description: '描述',
  food_state: '食物状态',
  food_processing: '加工状态',
  food_count: '个数',
  total_mass_g: '总质量',
  water_mass_g: '含水质量',
  main_ingredient_mass_g: '主食材质量',
}

/** 「类别、标签」: what a patch changes, in words. */
export function describePatch(patch: ImagePatch): string {
  return (Object.keys(patch) as Array<keyof ImagePatch>).map(key => PATCH_LABELS[key]).join('、')
}
