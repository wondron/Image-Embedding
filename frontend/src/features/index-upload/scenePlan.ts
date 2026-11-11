/**
 * Scenes of an upload. The server decides membership from the scene rules when it stores an image
 * (category subtree or cuisine; backend app/services/retrieval/scene_membership.py); the form
 * previews that result for the chosen category and lets the person go against it. Such a choice
 * is kept as an override and, once the image is stored, written as a manual include / exclude
 * (PUT /api/images/{id}/scene-memberships/{scene_id}) - only where the server's result differs.
 */
import { categoryAncestors, effectiveCuisines, type CategoryTree } from '../../entities/category'
import type { Scene } from '../../entities/scene'
import type { SceneOverride } from './annotation'

export interface ScenePlanRow {
  scene: Scene
  /** include_all: every image is in it, nothing to choose. */
  locked: boolean
  /** What the scene's rules give the chosen category. */
  ruleMember: boolean
  /** The person's choice where it differs from the rules; null otherwise. */
  override: SceneOverride | null
  member: boolean
}

/**
 * True when a rule of the scene covers the category: a category rule on the category or an
 * ancestor, or a cuisine rule on one of its effective cuisines (its own, else the nearest
 * ancestor's). No category, or one the tree does not have: no rule applies.
 */
export function ruleMatches(scene: Scene, categoryId: number | null, tree: CategoryTree | null): boolean {
  if (categoryId === null || !tree?.byId.has(categoryId)) return false
  const lineage = new Set([categoryId, ...categoryAncestors(tree, categoryId).map(node => node.id)])
  const cuisines = effectiveCuisines(tree, categoryId).value
  return scene.rules.some(rule => {
    if (rule.category) return lineage.has(rule.category.id)
    const cuisine = rule.cuisine?.trim()
    return cuisine ? cuisines.includes(cuisine) : false
  })
}

/** Every active scene (inactive ones have no members), in catalog order. */
export function planScenes(
  scenes: readonly Scene[],
  categoryId: number | null,
  tree: CategoryTree | null,
  overrides: Readonly<Record<string, SceneOverride>>,
): ScenePlanRow[] {
  return scenes.filter(scene => scene.isActive).map(scene => {
    if (scene.includeAll) return { scene, locked: true, ruleMember: true, override: null, member: true }
    const ruleMember = ruleMatches(scene, categoryId, tree)
    const chosen = overrides[scene.code]
    // A choice equal to the rule result changes nothing
    const override = chosen === 'include' && !ruleMember ? 'include' : chosen === 'exclude' && ruleMember ? 'exclude' : null
    return { scene, locked: false, ruleMember, override, member: override ? override === 'include' : ruleMember }
  })
}

/** Flip one row: away from the rule result it becomes an override, back to it the override goes. */
export function toggleSceneOverride(
  overrides: Readonly<Record<string, SceneOverride>>,
  row: ScenePlanRow,
): Record<string, SceneOverride> {
  const next = { ...overrides }
  if (row.locked) return next
  const member = !row.member
  if (member === row.ruleMember) delete next[row.scene.code]
  else next[row.scene.code] = member ? 'include' : 'exclude'
  return next
}

/** The overrides that still change something (e.g. after a new category already gives a scene). */
export function effectiveOverrides(
  scenes: readonly Scene[],
  categoryId: number | null,
  tree: CategoryTree | null,
  overrides: Readonly<Record<string, SceneOverride>>,
): Record<string, SceneOverride> {
  const out: Record<string, SceneOverride> = {}
  for (const row of planScenes(scenes, categoryId, tree, overrides)) if (row.override) out[row.scene.code] = row.override
  return out
}

export interface MembershipStep {
  sceneId: number
  code: string
  name: string
  isMember: boolean
}

/**
 * The manual membership writes that make a stored image's scenes what the form showed: one per
 * scene the person chose, and only where the server's result (it applied the rules itself)
 * differs. Scenes without a choice keep the server's result.
 */
export function membershipSteps(
  overrides: Readonly<Record<string, SceneOverride>>,
  resultScenes: readonly string[],
  scenes: readonly Scene[],
): MembershipStep[] {
  const current = new Set(resultScenes)
  return scenes.filter(scene => scene.isActive && !scene.includeAll && overrides[scene.code]).flatMap(scene => {
    const isMember = overrides[scene.code] === 'include'
    return current.has(scene.code) === isMember ? [] : [{ sceneId: scene.id, code: scene.code, name: scene.name, isMember }]
  })
}
