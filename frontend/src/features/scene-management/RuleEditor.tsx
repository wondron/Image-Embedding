import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { buildCategoryTree, listCategories, type CategoryNode } from '../../entities/category'
import type { RuleInput, Scene, SceneRule } from '../../entities/scene'
import { sceneError } from '../../entities/scene'
import { Dialog } from '../../components/Dialog'
import { CategoryPicker } from '../category-picker'

type Kind = 'category' | 'cuisine'
const cuisinePattern = /^[a-z][a-z0-9_]*$/

/**
 * Create a rule, or change one in place: the server replaces the target and / or the label in one
 * PATCH, so there is no half-done state to recover from.
 *
 * A rule does two things: its target (a category with everything below it, or a cuisine) puts
 * images in the scene, and a category rule may name the label their results show. In a 全包含
 * scene only the second counts.
 */
export function RuleEditor({ scene, rule, apiBase, busy, onClose, onSave }: { scene: Scene; rule?: SceneRule; apiBase: string; busy: boolean; onClose: () => void; onSave: (input: RuleInput) => Promise<boolean> }) {
  const [kind, setKind] = useState<Kind>(rule && rule.category === null ? 'cuisine' : 'category')
  const [categoryId, setCategoryId] = useState<number | null>(rule?.category?.id ?? null)
  const [cuisine, setCuisine] = useState(rule?.cuisine ?? '')
  const [labelId, setLabelId] = useState<number | null>(rule?.label?.id ?? null)
  const [categories, setCategories] = useState<CategoryNode[]>([])
  const [categoryError, setCategoryError] = useState('')
  const [categoryReady, setCategoryReady] = useState(false)
  const [reload, setReload] = useState(0)
  const [invalid, setInvalid] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setCategoryReady(false)
    listCategories({ baseUrl: apiBase, signal: controller.signal })
      .then(r => { if (!controller.signal.aborted) { setCategories(r.categories); setCategoryReady(true); setCategoryError('') } })
      .catch(e => { if (!controller.signal.aborted) setCategoryError(sceneError(e)) })
    return () => controller.abort()
  }, [apiBase, reload])
  // The whole tree, searched or opened level by level (a flat select of hundreds of names says nothing about where each sits)
  const categoryTree = useMemo(() => (categoryReady ? buildCategoryTree(categories) : null), [categories, categoryReady])

  const input: RuleInput = kind === 'category'
    ? { categoryId, cuisine: null, labelId }
    : { categoryId: null, cuisine: cuisine.trim() || null, labelId: null }
  const savedCategory = rule?.category?.id ?? null
  const targetChanged = Boolean(rule) && (input.categoryId !== savedCategory || input.cuisine !== (rule?.cuisine ?? null))
  const membershipChanges = scene.isActive && !scene.includeAll && (!rule || targetChanged)
  /** In a 全包含 scene a rule only maps a label: one without a label does nothing. */
  const idleInWholeScene = scene.includeAll && input.labelId === null

  async function submit(e: FormEvent) {
    e.preventDefault(); setInvalid('')
    if (kind === 'category') {
      if (categoryId === null) { setInvalid('请选择类别。'); return }
      if (categoryId !== savedCategory && !(categoryReady && categories.some(c => c.id === categoryId))) { setInvalid('类别没有加载出来，或所选类别已经不存在，请重试加载类别。'); return }
      if (labelId !== null && !scene.labels.some(l => l.id === labelId)) { setInvalid('场景标签必须是这个场景自己的标签。'); return }
    } else if (!input.cuisine || !cuisinePattern.test(input.cuisine) || input.cuisine.length > 32) {
      setInvalid('菜系填 1–32 位编码：小写字母开头，可以有数字和下划线。'); return
    }
    if (rule && !targetChanged && input.labelId === (rule.label?.id ?? null)) { setInvalid('没有需要保存的变更。'); return }
    if (await onSave(input)) onClose()
  }

  return <Dialog title={rule ? '编辑规则' : '新建规则'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="scene-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={busy}>
      <legend>匹配哪些图片</legend>
      <div className="rule-kind" role="radiogroup" aria-label="规则目标类型">
        <label><input type="radio" name="rule-kind" checked={kind === 'category'} onChange={() => setKind('category')} />按类别（连同全部下级类别）</label>
        <label><input type="radio" name="rule-kind" checked={kind === 'cuisine'} onChange={() => { setKind('cuisine'); setLabelId(null) }} />按菜系</label>
      </div>
      {kind === 'category' ? <>
        <CategoryPicker
          id="scene-rule-category"
          label="类别"
          tree={categoryTree}
          loading={!categoryReady && !categoryError}
          loadError={categoryError ? `类别加载失败：${categoryError}` : null}
          onRetry={() => setReload(v => v + 1)}
          value={categoryId}
          onChange={setCategoryId}
          disabled={busy}
          includeInactive
          recentKey={null}
          missingLabel={rule?.category && rule.category.id === categoryId ? `${rule.category.name ?? rule.category.code}（已保存引用）` : undefined}
          dialogInitialFocus={!rule}
        />
        <label>场景标签
          <select data-dialog-initial-focus={rule ? true : undefined} value={labelId ?? ''} onChange={e => setLabelId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">不指定</option>
            {scene.labels.map(l => <option key={l.id} value={l.id}>{l.name}（{l.code}）{l.isActive ? '' : ' · 停用'}</option>)}
          </select>
          <span className="scene-hint">{scene.labels.length
            ? '这个类别的图片，搜索结果的「场景标签」显示这个标签；不指定时沿用上级类别的规则，都没有就显示「未映射」。'
            : '这个场景还没有场景标签，可以先不指定；到「场景标签」页签里新建后再回来选。'}</span>
        </label>
      </> : <label>菜系编码
        <input data-dialog-initial-focus maxLength={32} placeholder="如 chinese" value={cuisine} onChange={e => setCuisine(e.target.value)} />
        <span className="scene-hint">类别的菜系（自己没有填的沿用上级类别的）包含这个编码，就算命中。按菜系的规则不能指定场景标签。</span>
      </label>}
    </fieldset>
    {scene.includeAll && <p className="mg-help">{idleInWholeScene
      ? '这是全包含场景，全部图片都已收录，规则只用来对应场景标签：没有指定标签的规则不起作用。'
      : '这是全包含场景，全部图片都已收录；这条规则只决定场景标签，立即生效，不需要同步。'}</p>}
    {!scene.includeAll && !scene.isActive && <p className="mg-help">场景已停用，规则先保存着；启用并全量同步后才开始收录图片。</p>}
    {membershipChanges && <p className="mg-help">{rule ? '匹配条件的改动直接保存在这条规则上；' : ''}已入库的图片要全量同步后才按新规则归属。</p>}
    {rule && !targetChanged && !scene.includeAll && <p className="mg-help">只改场景标签：搜图立即使用，不需要同步。</p>}
    {invalid && <p className="mg-form-error" role="alert">{invalid}</p>}
    <div className="dialog-actions"><button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="mg-btn mg-primary" disabled={busy}>保存</button></div>
  </form></Dialog>
}
