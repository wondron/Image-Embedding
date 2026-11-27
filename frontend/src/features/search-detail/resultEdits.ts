/**
 * Annotation saved from a search result's detail (the stored image's editor) after the results came
 * in. The results stay what the search returned; these overlay the cards of the images edited since,
 * so a corrected category shows at once without searching again. The similarity stays the search's:
 * editing the annotation does not change the image's vector.
 */
import type { CategoryTree } from '../../entities/category'
import type { FoodState, HeatingGroup, ImageDetail, LabelStatus } from '../../entities/image'
import type { Scene } from '../../entities/scene'
import { displayCategoryLabel, type SearchImage } from '../../entities/search-result'

/** How search results join an image's tags (backend TAG_SEPARATOR). */
export const RESULT_TAG_SEPARATOR = '、'

/** What a result card shows of a stored image, as saved. */
export interface SavedAnnotation {
  categoryId: number | null
  /** The category's display name. */
  category: string | null
  labelStatus: LabelStatus | null
  /** Joined with 「、」, as search results send them. */
  tags: string
  description: string
  scenes: string[]
  foodState: FoodState | null
  heatingGroup: HeatingGroup | null
  /** The searched scene's label for the category (scene_label). */
  sceneLabel: string | null
}

/** By stored image id; null: the image was deleted. */
export type ResultEdits = ReadonlyMap<string, SavedAnnotation | null>
export const NO_RESULT_EDITS: ResultEdits = new Map()

const STORED_IMAGE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Whether a result is a stored image that can be opened in the editor: a self-built hit with the
 * image's id (UUID). Other channels' ids are made up (「image-N」), and so is a self-built hit's
 * whose id the server did not send.
 */
export function isEditableResult(item: Pick<SearchImage, 'id' | 'source'>): boolean {
  return item.source === 'self' && STORED_IMAGE_ID.test(item.id)
}

/**
 * The label a scene gives a category in search results (backend map_categories_to_scene_labels,
 * contract §2.8): of the scene's rules on the category or one of its ancestors that map to an
 * active label, the deepest one's. null when no rule does; also when the category is not in the
 * tree, which cannot tell its ancestors.
 */
export function sceneLabelFor(scene: Pick<Scene, 'labels' | 'rules'>, categoryId: number, tree: CategoryTree): string | null {
  if (!tree.byId.has(categoryId)) return null
  const active = new Map(scene.labels.filter(label => label.isActive).map(label => [label.id, label.name]))
  const byCategory = new Map<number, string>()
  for (const rule of scene.rules) {
    const name = rule.category && rule.label ? active.get(rule.label.id) : undefined
    if (rule.category && name !== undefined) byCategory.set(rule.category.id, name)
  }
  const seen = new Set<number>()
  for (let id: number | null = categoryId; id !== null && !seen.has(id); id = tree.byId.get(id)?.node.parentId ?? null) {
    seen.add(id)
    const name = byCategory.get(id)
    if (name !== undefined) return name
  }
  return null
}

/**
 * The scene label a saved image's card shows: the card's own while the category is the same (the
 * server gave it), else the searched scene's rules applied to the new category. null without a
 * category, or when the scene or the category tree is not at hand.
 */
export function savedSceneLabel(
  card: Pick<SearchImage, 'categoryId' | 'sceneLabel'>,
  image: Pick<ImageDetail, 'categoryId'>,
  scene: Pick<Scene, 'labels' | 'rules'> | undefined,
  tree: CategoryTree | null,
): string | null {
  if (image.categoryId === card.categoryId) return card.sceneLabel
  if (image.categoryId === null || !scene || !tree) return null
  return sceneLabelFor(scene, image.categoryId, tree)
}

export function savedAnnotation(image: ImageDetail, sceneLabel: string | null): SavedAnnotation {
  return {
    categoryId: image.categoryId,
    category: image.category,
    labelStatus: image.labelStatus,
    tags: image.tags.join(RESULT_TAG_SEPARATOR),
    description: image.description,
    scenes: [...image.scenes],
    foodState: image.foodState,
    heatingGroup: image.heatingGroup,
    sceneLabel,
  }
}

export function withSaved(edits: ResultEdits, id: string, saved: SavedAnnotation): ResultEdits {
  return new Map(edits).set(id, saved)
}

export function withDeleted(edits: ResultEdits, id: string): ResultEdits {
  return new Map(edits).set(id, null)
}

export interface EditedResults {
  /** The results with the saved annotation on their cards; deleted images left out. */
  items: SearchImage[]
  /** Results whose card now differs from what the search returned. */
  edited: ReadonlySet<string>
}

const NONE: ReadonlySet<string> = new Set()

/** The search's results as they are now: saved annotation applied, deleted images left out. */
export function applyResultEdits(items: readonly SearchImage[], edits: ResultEdits): EditedResults {
  if (edits.size === 0) return { items: [...items], edited: NONE }
  const shown: SearchImage[] = []
  const edited = new Set<string>()
  for (const item of items) {
    const saved = item.source === 'self' ? edits.get(item.id) : undefined
    if (saved === undefined) { shown.push(item); continue }
    if (saved === null) continue
    const next = withAnnotation(item, saved)
    if (cardDiffers(item, next)) edited.add(item.id)
    shown.push(next)
  }
  return { items: shown, edited }
}

function withAnnotation(item: SearchImage, saved: SavedAnnotation): SearchImage {
  return {
    ...item,
    categoryId: saved.categoryId,
    category: saved.category,
    labelStatus: saved.labelStatus,
    tags: saved.tags,
    description: saved.description,
    scenes: saved.scenes,
    sceneLabel: saved.sceneLabel,
    // Only scenes that output heating send these with their results
    ...(item.outputsHeating ? { foodState: saved.foodState, heatingGroup: saved.heatingGroup } : {}),
    // As the search names a self-built hit (entities/search-result toSearchImage)
    title: displayCategoryLabel({ sceneLabel: saved.sceneLabel, category: saved.category }) ?? (saved.tags || '未命名图片'),
  }
}

/** Scene codes in any order. */
const sceneSet = (codes: readonly string[]) => [...codes].sort().join('\n')

function cardDiffers(before: SearchImage, after: SearchImage): boolean {
  return before.categoryId !== after.categoryId
    || before.labelStatus !== after.labelStatus
    || before.sceneLabel !== after.sceneLabel
    || before.tags !== after.tags
    || before.description !== after.description
    || sceneSet(before.scenes) !== sceneSet(after.scenes)
    || before.foodState !== after.foodState
    || before.heatingGroup?.id !== after.heatingGroup?.id
}
