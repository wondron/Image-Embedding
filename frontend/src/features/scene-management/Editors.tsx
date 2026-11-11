import { useState, type FormEvent } from 'react'
import { Dialog } from '../../components/Dialog'
import type { Scene, SceneInput, SceneLabel, LabelInput } from '../../entities/scene'
const codePattern = /^[a-z][a-z0-9_]*$/
export function SceneEditor({ scene, protectedScene, busy, error, onClose, onSave }: { scene?: Scene; protectedScene: boolean; busy: boolean; error: string; onClose: () => void; onSave: (input: SceneInput) => Promise<boolean> }) {
  const [form, setForm] = useState<SceneInput>(scene ? { code: scene.code, name: scene.name, description: scene.description, includeAll: scene.includeAll, outputsHeating: scene.outputsHeating, isActive: scene.isActive } : { code: '', name: '', description: null, includeAll: false, outputsHeating: false, isActive: true })
  const [invalid, setInvalid] = useState(''); const [confirmed, setConfirmed] = useState(false)
  const changesFlags = scene ? scene.includeAll !== form.includeAll || scene.isActive !== form.isActive : form.includeAll
  async function submit(e: FormEvent) {
    e.preventDefault(); setInvalid('')
    if (!codePattern.test(form.code.trim()) || form.code.trim().length > 32 || ['normal', 'device'].includes(form.code.trim()) || !form.name.trim() || form.name.trim().length > 64 || (form.description?.length ?? 0) > 256) { setInvalid('请填写合法 code（1–32，小写字母开头及数字/下划线，不可 normal/device）、名称（1–64）和描述（≤256）。'); return }
    if (scene && JSON.stringify(form) === JSON.stringify({ code: scene.code, name: scene.name, description: scene.description, includeAll: scene.includeAll, outputsHeating: scene.outputsHeating, isActive: scene.isActive })) { setInvalid('没有需要保存的变更。'); return }
    if (changesFlags && !confirmed) { setConfirmed(true); return }
    if (await onSave(form)) onClose()
  }
  return <Dialog title={scene ? '编辑场景' : '新建场景'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="mg-editor scene-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={busy}><label>code<input data-dialog-initial-focus required aria-invalid={error.includes('SCENE_CODE_EXISTS')} readOnly={Boolean(scene)} maxLength={32} value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} /></label><label>名称<input required maxLength={64} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>描述<textarea maxLength={256} value={form.description ?? ''} onChange={e => setForm({ ...form, description: e.target.value || null })} /></label>
      <label><input type="checkbox" disabled={protectedScene} checked={form.includeAll} onChange={e => { setConfirmed(false); setForm({ ...form, includeAll: e.target.checked }) }} />全包含</label>
      <label><input type="checkbox" checked={form.outputsHeating} onChange={e => setForm({ ...form, outputsHeating: e.target.checked })} />输出加热信息</label>
      <label><input type="checkbox" disabled={protectedScene} checked={form.isActive} onChange={e => { setConfirmed(false); setForm({ ...form, isActive: e.target.checked }) }} />启用</label>
    </fieldset>
    {protectedScene && <p>默认场景不可停用、删除或取消全包含（业务保护，不是权限）。</p>}
    {confirmed && <div role="alert">{(!scene || scene.includeAll !== form.includeAll) && <p>{form.includeAll ? '开启全包含后忽略规则与人工覆盖，包含该场景全部图片。' : '切回按规则计算后，此前被忽略的旧人工覆盖可能恢复。'}</p>}{scene?.isActive !== form.isActive && <p>{form.isActive ? '启用后需显式全量同步，检索索引才会一致。' : '停用后无有效成员、不可检索；规则与人工覆盖保留，恢复后需同步。'}</p>}<p>确定保存以上变更？数据库保存后仍需显式全量同步。</p></div>}
    {(invalid || error) && <p className="mg-form-error" role="alert">{invalid || error}</p>}
    <div className="dialog-actions"><button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="mg-btn mg-primary" disabled={busy}>{confirmed ? '确认保存' : changesFlags ? '下一步：确认变更' : '保存'}</button></div>
  </form></Dialog>
}
export function LabelEditor({ label, busy, error, onClose, onSave }: { label?: SceneLabel; busy: boolean; error: string; onClose: () => void; onSave: (input: LabelInput) => Promise<boolean> }) {
  const [form, setForm] = useState<LabelInput>(label ? { code: label.code, name: label.name, sortOrder: label.sortOrder, isActive: label.isActive } : { code: '', name: '', sortOrder: 0, isActive: true }); const [invalid, setInvalid] = useState('')
  async function submit(e: FormEvent) { e.preventDefault(); if (!codePattern.test(form.code.trim()) || form.code.trim().length > 64 || !form.name.trim() || form.name.trim().length > 64 || !Number.isInteger(form.sortOrder) || form.sortOrder < -2147483648 || form.sortOrder > 2147483647) { setInvalid('code/name 必填（≤64），code 小写字母开头及数字/下划线；sortOrder 须为 int32。'); return }; if (await onSave(form)) onClose() }
  return <Dialog title={label ? '编辑标签' : '新建标签'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="scene-form" onSubmit={e => void submit(e)}><fieldset disabled={busy}><label>code<input data-dialog-initial-focus required aria-invalid={error.includes('SCENE_CODE_EXISTS')} maxLength={64} value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} /></label><label>名称<input required maxLength={64} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>排序<input required type="number" step="1" min={-2147483648} max={2147483647} value={Number.isNaN(form.sortOrder) ? '' : form.sortOrder} onChange={e => setForm({ ...form, sortOrder: e.target.value === '' ? NaN : Number(e.target.value) })} /></label><label><input type="checkbox" checked={form.isActive} onChange={e => setForm({ ...form, isActive: e.target.checked })} />启用</label></fieldset>{(invalid || error) && <p className="mg-form-error" role="alert">{invalid || error}</p>}<div className="dialog-actions"><button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="mg-btn mg-primary" disabled={busy}>保存</button></div></form></Dialog>
}
