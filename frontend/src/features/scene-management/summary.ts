import type { Scene, SceneCatalog, SceneLabel, SceneRule } from '../../entities/scene'

/**
 * What a scene does, told from its configuration: the wording the page shows so a scene explains
 * itself (which images a search in it looks through, which labels its results carry).
 */

/** 12345 → 「12,345」. */
export function countText(value: number): string {
  return value.toLocaleString('zh-CN')
}

/**
 * How many images the library holds, as the catalog tells it: an active 全包含 scene has every
 * image as a member (the default scene always is one). null: no such scene, the size is unknown.
 */
export function libraryImageCount(catalog: Pick<SceneCatalog, 'scenes'> | null | undefined): number | null {
  const whole = catalog?.scenes.filter(scene => scene.isActive && scene.includeAll) ?? []
  return whole.length ? Math.max(...whole.map(scene => scene.memberCount)) : null
}

export interface SceneCoverage {
  /** Images in the scene now (a rule-based scene's count is as of the last full sync). */
  members: number
  /** The library's size; null when unknown. */
  total: number | null
  /** Share of the library, 0–100; null when the size is unknown or the library is empty. */
  percent: number | null
  /** 「37%」, 「<1%」, 「>99%」; '' when percent is null. */
  percentText: string
}

/** Which part of the library a scene holds. */
export function sceneCoverage(scene: Pick<Scene, 'memberCount' | 'includeAll' | 'isActive'>, total: number | null): SceneCoverage {
  const members = Math.max(0, scene.memberCount)
  if (total === null || total <= 0) return { members, total, percent: null, percentText: '' }
  if (scene.isActive && scene.includeAll) return { members, total, percent: 100, percentText: '100%' }
  const raw = Math.min(100, (members / total) * 100)
  const percentText = members === 0 ? '0%'
    : raw < 1 ? '<1%'
      : raw > 99 && members < total ? '>99%'
        : `${Math.round(raw)}%`
  return { members, total, percent: raw, percentText }
}

/**
 * How wide the coverage bar is drawn, in percent: a scene that holds a few images of a large
 * library still shows a sliver, an empty one nothing.
 */
export function meterWidth(coverage: SceneCoverage): number {
  if (coverage.percent === null || coverage.members === 0) return 0
  return Math.max(coverage.percent, 2)
}

/** The labels of a scene that can show on a result: active, and named by at least one rule. */
export function labelsInUse(scene: Pick<Scene, 'labels' | 'rules'>): SceneLabel[] {
  const named = new Set(scene.rules.flatMap(rule => (rule.label ? [rule.label.id] : [])))
  return scene.labels.filter(label => label.isActive && named.has(label.id))
}

/** The scene's own record of a rule's label (its name as it is now, whether it is active); null: no label, or it is gone. */
export function ruleLabel(scene: Pick<Scene, 'labels'>, rule: SceneRule): SceneLabel | null {
  return rule.label ? scene.labels.find(label => label.id === rule.label!.id) ?? null : null
}

/** 「只收录命中规则的图片」 in a few words, for the list. */
export function scopeText(scene: Pick<Scene, 'includeAll' | 'isActive' | 'rules'>): string {
  if (!scene.isActive) return '已停用，搜图时不可选'
  if (scene.includeAll) return '全部图片'
  return scene.rules.length ? `按 ${scene.rules.length} 条规则收录` : '还没有收录规则'
}

/**
 * The scene in plain sentences: what a search in it looks through, then what its results show.
 * Read in this order they answer 「这个场景是干什么的」.
 */
export function sceneBrief(scene: Scene): string[] {
  if (!scene.isActive) {
    return [
      '这个场景已停用：搜图时不能选它，也没有图片归属它。',
      '规则、标签和人工调整都保留着，重新启用并全量同步后恢复。',
    ]
  }
  const sentences: string[] = []
  const members = countText(Math.max(0, scene.memberCount))
  if (scene.includeAll) {
    sentences.push(`搜图时选这个场景，会在图库的全部图片里检索（现在 ${members} 张）。`)
  } else if (scene.memberCount > 0) {
    sentences.push(scene.rules.length
      ? `搜图时选这个场景，只在它收录的 ${members} 张图片里检索：类别或菜系命中 ${scene.rules.length} 条收录规则的图片，加上单张图片上的人工调整。`
      : `搜图时选这个场景，只在它收录的 ${members} 张图片里检索。现在没有收录规则，这些图片是人工纳入的，或者是上次全量同步前的结果。`)
  } else {
    sentences.push(scene.rules.length
      ? `这个场景有 ${scene.rules.length} 条收录规则，但现在还没有图片归属它，搜图不会有结果。刚改过规则的话，需要全量同步一次。`
      : '这个场景还没有收录规则，现在没有图片，搜图不会有结果。先在「收录规则」里添加规则。')
  }
  const used = labelsInUse(scene).length
  if (scene.labels.length === 0) {
    sentences.push('没有设置场景标签，搜索结果的「场景标签」一栏显示「未映射」。')
  } else if (used === 0) {
    sentences.push(`设置了 ${scene.labels.length} 个场景标签，但还没有规则把类别对应到启用中的标签，搜索结果仍显示「未映射」。`)
  } else {
    sentences.push(`搜索结果按规则显示场景标签（${used} 个在用），规则没有覆盖到的类别显示「未映射」。`)
  }
  if (scene.outputsHeating) sentences.push('结果还会附带加热组和食物状态。')
  return sentences
}
