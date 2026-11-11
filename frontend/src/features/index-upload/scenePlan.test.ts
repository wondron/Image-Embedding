import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import type { Scene, SceneRule } from '../../entities/scene'
import { effectiveOverrides, membershipSteps, planScenes, ruleMatches, toggleSceneOverride } from './scenePlan'

function node(id: number, code: string, parentId: number | null, cuisines: string[] = []): CategoryNode {
  return {
    id, code, parentId, path: String(id), name: code, names: [], cuisines, heatingGroup: null,
    sortOrder: 0, isActive: true, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
  }
}

function scene(id: number, code: string, rules: Array<Partial<SceneRule>>, extra: Partial<Scene> = {}): Scene {
  return {
    id, code, name: code.toUpperCase(), description: null, includeAll: false, outputsHeating: false, isActive: true,
    createdAt: null, memberCount: 0, labels: [],
    rules: rules.map((rule, index) => ({ id: index + 1, category: null, cuisine: null, label: null, ...rule })),
    ...extra,
  }
}

// dish (cuisine chinese) > dish.meat > dish.meat.steak (own cuisine american); dessert
const tree = buildCategoryTree([
  node(1, 'dish', null, ['chinese']),
  node(2, 'dish.meat', 1),
  node(3, 'dish.meat.steak', 2, ['american']),
  node(4, 'dessert', null),
])
const ref = (id: number) => ({ id, code: `c${id}`, name: null })
const scenes = [
  scene(1, 'general', [], { includeAll: true }),
  scene(2, 'oven', [{ category: ref(2) }]),  // the meat subtree
  scene(3, 'cn_diet', [{ cuisine: 'chinese' }]),
  scene(4, 'us_diet', [{ cuisine: 'american' }]),
  scene(5, 'off', [{ category: ref(1) }], { isActive: false }),
]
const plan = (categoryId: number | null, overrides = {}) =>
  Object.fromEntries(planScenes(scenes, categoryId, tree, overrides).map(row => [row.scene.code, row.member]))

describe('ruleMatches (same rules as the server)', () => {
  it('covers a category rule subtree and the effective cuisines', () => {
    expect(ruleMatches(scenes[1], 3, tree)).toBe(true)  // steak is under meat
    expect(ruleMatches(scenes[1], 1, tree)).toBe(false)  // dish is above meat
    expect(ruleMatches(scenes[2], 2, tree)).toBe(true)  // meat has no cuisine: dish's chinese applies
    expect(ruleMatches(scenes[2], 3, tree)).toBe(false)  // steak's own american replaces it
    expect(ruleMatches(scenes[3], 3, tree)).toBe(true)
    expect(ruleMatches(scenes[1], null, tree)).toBe(false)
    expect(ruleMatches(scenes[1], 99, tree)).toBe(false)
  })
})

describe('planScenes', () => {
  it('previews the rule result; inactive scenes are left out, include_all is fixed', () => {
    expect(plan(3)).toEqual({ general: true, oven: true, cn_diet: false, us_diet: true })
    expect(plan(null)).toEqual({ general: true, oven: false, cn_diet: false, us_diet: false })
    const general = planScenes(scenes, 3, tree, {})[0]
    expect(general.locked).toBe(true)
    expect(toggleSceneOverride({}, general)).toEqual({})
  })

  it('applies choices against the rules and ignores the ones that change nothing', () => {
    expect(plan(3, { oven: 'exclude', cn_diet: 'include', us_diet: 'include' })).toEqual({ general: true, oven: false, cn_diet: true, us_diet: true })
    const rows = planScenes(scenes, 3, tree, { us_diet: 'include' })
    expect(rows.find(row => row.scene.code === 'us_diet')?.override).toBeNull()
  })

  it('toggles a scene away from the rules and back', () => {
    let overrides = toggleSceneOverride({}, planScenes(scenes, 3, tree, {})[1])
    expect(overrides).toEqual({ oven: 'exclude' })
    const oven = planScenes(scenes, 3, tree, overrides).find(row => row.scene.code === 'oven')!
    expect([oven.member, oven.override]).toEqual([false, 'exclude'])
    overrides = toggleSceneOverride(overrides, oven)
    expect(overrides).toEqual({})
    const dessertCn = planScenes(scenes, 4, tree, {}).find(row => row.scene.code === 'cn_diet')!
    expect(toggleSceneOverride({}, dessertCn)).toEqual({ cn_diet: 'include' })
  })

  it('drops choices a new category already gives', () => {
    expect(effectiveOverrides(scenes, 2, tree, { oven: 'include', cn_diet: 'exclude', us_diet: 'include' }))
      .toEqual({ cn_diet: 'exclude', us_diet: 'include' })  // meat: oven by rule already
  })
})

describe('membershipSteps', () => {
  it('writes only the choices the server result does not have yet', () => {
    const steps = membershipSteps({ oven: 'exclude', cn_diet: 'include', us_diet: 'include' }, ['general', 'oven', 'us_diet'], scenes)
    expect(steps).toEqual([
      { sceneId: 2, code: 'oven', name: 'OVEN', isMember: false },
      { sceneId: 3, code: 'cn_diet', name: 'CN_DIET', isMember: true },
    ])
    expect(membershipSteps({ off: 'include', general: 'exclude' }, ['general'], scenes)).toEqual([])  // inactive / include_all
  })
})
