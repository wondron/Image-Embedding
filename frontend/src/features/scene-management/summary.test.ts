import { describe, expect, it } from 'vitest'
import type { Scene, SceneLabel, SceneRule } from '../../entities/scene'
import { labelsInUse, libraryImageCount, meterWidth, ruleLabel, sceneBrief, sceneCoverage, scopeText } from './summary'

const scene = (fields: Partial<Scene> = {}): Scene => ({
  id: 2, code: 'oven', name: '一体机', description: null, includeAll: false, outputsHeating: false, isActive: true,
  createdAt: null, memberCount: 0, labels: [], rules: [], ...fields,
})
const label = (id: number, isActive = true): SceneLabel => ({ id, code: `label_${id}`, name: `标签${id}`, sortOrder: id, isActive, ruleCount: 0 })
const categoryRule = (id: number, labelId: number | null = null): SceneRule => ({
  id, category: { id: 10 + id, code: `dish.c${id}`, name: `类别${id}` }, cuisine: null,
  label: labelId === null ? null : { id: labelId, code: `label_${labelId}`, name: `标签${labelId}` },
})
const cuisineRule = (id: number): SceneRule => ({ id, category: null, cuisine: 'chinese', label: null })

describe('libraryImageCount', () => {
  it('reads the library size off an active 全包含 scene, and does not guess without one', () => {
    const general = scene({ id: 1, code: 'general', includeAll: true, memberCount: 128 })
    expect(libraryImageCount({ scenes: [general, scene({ memberCount: 64 })] })).toBe(128)
    // A stopped scene has no members whatever it would hold
    expect(libraryImageCount({ scenes: [scene({ includeAll: true, isActive: false, memberCount: 0 }), scene({ memberCount: 64 })] })).toBeNull()
    expect(libraryImageCount(null)).toBeNull()
  })
})

describe('sceneCoverage', () => {
  it('says which part of the library a scene holds', () => {
    expect(sceneCoverage(scene({ memberCount: 1 }), 4)).toMatchObject({ members: 1, total: 4, percent: 25, percentText: '25%' })
    expect(sceneCoverage(scene({ memberCount: 0 }), 4).percentText).toBe('0%')
    expect(sceneCoverage(scene({ memberCount: 1 }), 1000).percentText).toBe('<1%')
    expect(sceneCoverage(scene({ memberCount: 995 }), 1000).percentText).toBe('>99%')
    expect(sceneCoverage(scene({ memberCount: 1000 }), 1000).percentText).toBe('100%')
    expect(sceneCoverage(scene({ includeAll: true, memberCount: 128 }), 128)).toMatchObject({ percent: 100, percentText: '100%' })
  })

  it('claims no share when the library size is unknown or zero, and never more than all of it', () => {
    expect(sceneCoverage(scene({ memberCount: 3 }), null)).toMatchObject({ members: 3, percent: null, percentText: '' })
    expect(sceneCoverage(scene({ memberCount: 0 }), 0).percent).toBeNull()
    // The count of a rule-based scene is as of the last sync: images may have been deleted since
    expect(sceneCoverage(scene({ memberCount: 6 }), 4)).toMatchObject({ percent: 100, percentText: '100%' })
  })

  it('draws a sliver for a few images of a large library and nothing for none', () => {
    expect(meterWidth(sceneCoverage(scene({ memberCount: 1 }), 1000))).toBe(2)
    expect(meterWidth(sceneCoverage(scene({ memberCount: 0 }), 1000))).toBe(0)
    expect(meterWidth(sceneCoverage(scene({ memberCount: 1 }), 4))).toBe(25)
    expect(meterWidth(sceneCoverage(scene({ memberCount: 1 }), null))).toBe(0)
  })
})

describe('labels', () => {
  it('counts the labels a result can show: active and named by a rule', () => {
    const oven = scene({ labels: [label(1), label(2, false), label(3)], rules: [categoryRule(1, 1), categoryRule(2, 2), cuisineRule(3)] })
    expect(labelsInUse(oven).map(item => item.id)).toEqual([1])  // 2 is disabled, 3 is not named by any rule
    expect(ruleLabel(oven, oven.rules[1])?.isActive).toBe(false)
    expect(ruleLabel(oven, oven.rules[2])).toBeNull()
  })
})

describe('scopeText', () => {
  it('says in a few words which images a scene holds', () => {
    expect(scopeText(scene({ includeAll: true }))).toBe('全部图片')
    expect(scopeText(scene({ rules: [categoryRule(1), cuisineRule(2)] }))).toBe('按 2 条规则收录')
    expect(scopeText(scene())).toBe('还没有收录规则')
    expect(scopeText(scene({ isActive: false, includeAll: true }))).toBe('已停用，搜图时不可选')
  })
})

describe('sceneBrief', () => {
  it('tells what a search in a rule-based scene looks through and what its results show', () => {
    const text = sceneBrief(scene({
      memberCount: 1234, outputsHeating: true,
      labels: [label(1), label(2, false)], rules: [categoryRule(1, 1), cuisineRule(2)],
    })).join('')
    expect(text).toContain('只在它收录的 1,234 张图片里检索')
    expect(text).toContain('命中 2 条收录规则')
    expect(text).toContain('搜索结果按规则显示场景标签（1 个在用）')
    expect(text).toContain('结果还会附带加热组和食物状态。')
  })

  it('says every image for a 全包含 scene, and that results are unmapped without labels', () => {
    const text = sceneBrief(scene({ includeAll: true, memberCount: 128 })).join('')
    expect(text).toContain('会在图库的全部图片里检索（现在 128 张）')
    expect(text).toContain('没有设置场景标签，搜索结果的「场景标签」一栏显示「未映射」。')
    expect(text).not.toContain('加热')
  })

  it('says why a scene has nothing to search yet', () => {
    expect(sceneBrief(scene()).join('')).toContain('还没有收录规则，现在没有图片，搜图不会有结果')
    expect(sceneBrief(scene({ rules: [categoryRule(1)] })).join('')).toContain('有 1 条收录规则，但现在还没有图片归属它')
    // Members without rules: manual includes, or what the last sync left
    expect(sceneBrief(scene({ memberCount: 2 })).join('')).toContain('现在没有收录规则，这些图片是人工纳入的')
    // Labels exist but no rule names an active one
    expect(sceneBrief(scene({ memberCount: 2, labels: [label(1, false)], rules: [categoryRule(1, 1)] })).join(''))
      .toContain('设置了 1 个场景标签，但还没有规则把类别对应到启用中的标签')
  })

  it('describes a stopped scene as stopped, not as a search', () => {
    const sentences = sceneBrief(scene({ isActive: false, includeAll: true, memberCount: 0, outputsHeating: true }))
    expect(sentences.join('')).toBe('这个场景已停用：搜图时不能选它，也没有图片归属它。规则、标签和人工调整都保留着，重新启用并全量同步后恢复。')
  })
})
