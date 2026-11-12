import { useEffect, useState, type FormEvent } from 'react'
import { listCategories, type CategoryNode } from '../../entities/category'
import type { RuleInput, Scene, SceneRule } from '../../entities/scene'
import { sceneError } from '../../entities/scene'
import { Dialog } from '../../components/Dialog'

type Kind = 'category' | 'cuisine'
const cuisinePattern = /^[a-z][a-z0-9_]*$/

/**
 * Create a rule, or change one in place: the server replaces the target and / or the label in one
 * PATCH, so there is no half-done state to recover from.
 */
export function RuleEditor({ scene, rule, apiBase, busy, onClose, onSave }: { scene: Scene; rule?: SceneRule; apiBase: string; busy: boolean; onClose: () => void; onSave: (input: RuleInput) => Promise<boolean> }) {
  const [kind, setKind] = useState<Kind>(rule && rule.category === null ? 'cuisine' : 'category')
  const [categoryId, setCategoryId] = useState<number | null>(rule?.category?.id ?? null)
  const [cuisine, setCuisine] = useState(rule?.cuisine ?? '')
  const [labelId, setLabelId] = useState<number | null>(rule?.label?.id ?? null)
  const [categories, setCategories] = useState<CategoryNode[]>([]); const [categoryError, setCategoryError] = useState(''); const [categoryReady, setCategoryReady] = useState(false); const [reload, setReload] = useState(0)
  const [invalid, setInvalid] = useState('')
  useEffect(() => { const controller = new AbortController(); setCategoryReady(false); listCategories({ baseUrl: apiBase, signal: controller.signal }).then(r => { if (!controller.signal.aborted) { setCategories(r.categories); setCategoryReady(true); setCategoryError('') } }).catch(e => { if (!controller.signal.aborted) setCategoryError(sceneError(e)) }); return () => controller.abort() }, [apiBase, reload])

  const input: RuleInput = kind === 'category'
    ? { categoryId, cuisine: null, labelId }
    : { categoryId: null, cuisine: cuisine.trim() || null, labelId: null }
  const savedCategory = rule?.category?.id ?? null
  const targetChanged = Boolean(rule) && (input.categoryId !== savedCategory || input.cuisine !== (rule?.cuisine ?? null))
  const membershipChanges = scene.isActive && !scene.includeAll && (!rule || targetChanged)

  async function submit(e: FormEvent) {
    e.preventDefault(); setInvalid('')
    if (kind === 'category') {
      if (categoryId === null) { setInvalid('请选择类别。'); return }
      if (categoryId !== savedCategory && !(categoryReady && categories.some(c => c.id === categoryId))) { setInvalid('类别数据源不可用或所选类别不存在，请重试加载类别。'); return }
      if (labelId !== null && !scene.labels.some(l => l.id === labelId)) { setInvalid('映射标签必须属于当前场景。'); return }
    } else if (!input.cuisine || !cuisinePattern.test(input.cuisine) || input.cuisine.length > 32) {
      setInvalid('菜系须为 1–32 位 code：小写字母开头，可含数字和下划线。'); return
    }
    if (rule && !targetChanged && input.labelId === (rule.label?.id ?? null)) { setInvalid('没有需要保存的变更。'); return }
    if (await onSave(input)) onClose()
  }

  return <Dialog title={rule ? '编辑规则' : '新建规则'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="scene-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={busy}>
      <legend>按什么分配</legend>
      <div className="rule-kind" role="radiogroup" aria-label="规则目标类型">
        <label><input type="radio" name="rule-kind" checked={kind === 'category'} onChange={() => setKind('category')} />按类别（含全部子类）</label>
        <label><input type="radio" name="rule-kind" checked={kind === 'cuisine'} onChange={() => { setKind('cuisine'); setLabelId(null) }} />按菜系</label>
      </div>
      {kind === 'category' ? <>
        <label>类别<select data-dialog-initial-focus value={categoryId ?? ''} onChange={e => setCategoryId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">请选择类别</option>
          {rule?.category && !categories.some(c => c.id === rule.category!.id) && <option value={rule.category.id}>{rule.category.name ?? rule.category.code}（已保存引用）</option>}
          {categories.map(c => <option key={c.id} value={c.id}>{c.name ?? c.code}（{c.code}）{c.isActive ? '' : ' · 停用'}</option>)}
        </select></label>
        <label>映射标签<select value={labelId ?? ''} onChange={e => setLabelId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">不映射</option>
          {scene.labels.map(l => <option key={l.id} value={l.id}>{l.name}（{l.code}）{l.isActive ? '' : ' · 停用'}</option>)}
        </select></label>
      </> : <>
        <label>菜系 code<input data-dialog-initial-focus maxLength={32} placeholder="如 chinese" value={cuisine} onChange={e => setCuisine(e.target.value)} /></label>
        <p className="mg-help">类别（含沿用上级的）菜系包含它即命中。按菜系的规则不能映射标签。</p>
      </>}
    </fieldset>
    {kind === 'category' && categoryError && <p className="mg-form-error">类别加载失败：{categoryError}<button type="button" className="text-button" onClick={() => setReload(v => v + 1)}>重试类别</button></p>}
    {membershipChanges && <p className="mg-help">{rule ? '目标改动会在原规则上直接保存；' : ''}已入库图片要在「同步」里重算后才按新规则归属。</p>}
    {rule && !targetChanged && <p className="mg-help">只改映射标签：检索立即使用，不需要同步。</p>}
    {invalid && <p className="mg-form-error" role="alert">{invalid}</p>}
    <div className="dialog-actions"><button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="mg-btn mg-primary" disabled={busy}>保存</button></div>
  </form></Dialog>
}
