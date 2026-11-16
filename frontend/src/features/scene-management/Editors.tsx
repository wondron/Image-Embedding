import { useState, type FormEvent } from 'react'
import { Dialog } from '../../components/Dialog'
import { notify } from '../../shared/ui'
import type { Scene, SceneInput, SceneLabel, LabelInput } from '../../entities/scene'

const codePattern = /^[a-z][a-z0-9_]*$/

function sceneInput(scene: Scene): SceneInput {
  return { code: scene.code, name: scene.name, description: scene.description, includeAll: scene.includeAll, outputsHeating: scene.outputsHeating, isActive: scene.isActive }
}

/** codeTaken: the server refused the code (SCENE_CODE_EXISTS); the reason itself is in the popup. */
export function SceneEditor({ scene, protectedScene, busy, codeTaken = false, onClose, onSave }: { scene?: Scene; protectedScene: boolean; busy: boolean; codeTaken?: boolean; onClose: () => void; onSave: (input: SceneInput) => Promise<boolean> }) {
  const [form, setForm] = useState<SceneInput>(scene ? sceneInput(scene) : { code: '', name: '', description: null, includeAll: false, outputsHeating: false, isActive: true })
  const [invalid, setInvalid] = useState('')
  /** Turning the scene on or off, or switching it between every image and its rules, is confirmed once more. */
  const [confirmed, setConfirmed] = useState(false)
  const changesFlags = scene ? scene.includeAll !== form.includeAll || scene.isActive !== form.isActive : form.includeAll

  async function submit(e: FormEvent) {
    e.preventDefault(); setInvalid('')
    const code = form.code.trim()
    if (!codePattern.test(code) || code.length > 32 || ['normal', 'device'].includes(code) || !form.name.trim() || form.name.trim().length > 64 || (form.description?.length ?? 0) > 256) {
      setInvalid('请检查：名称 1–64 个字；编码 1–32 位，小写字母开头，只含小写字母、数字和下划线，且不能是 normal 或 device；说明不超过 256 个字。')
      return
    }
    if (scene && JSON.stringify(form) === JSON.stringify(sceneInput(scene))) { notify.info('没有需要保存的变更。'); return }
    if (changesFlags && !confirmed) { setConfirmed(true); return }
    if (await onSave(form)) onClose()
  }

  return <Dialog title={scene ? '编辑场景' : '新建场景'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="scene-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={busy}>
      <label>名称
        <input data-dialog-initial-focus required maxLength={64} placeholder="如 一体机" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <span className="scene-hint">搜图时在场景下拉里看到的名字。</span>
      </label>
      <label>编码
        <input required aria-invalid={codeTaken} readOnly={Boolean(scene)} maxLength={32} placeholder="如 oven" value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} />
        <span className="scene-hint">{scene
          ? '搜图接口用它指定场景，创建后不能修改。'
          : '搜图接口用它指定场景，创建后不能修改。小写字母开头，可以有数字和下划线。'}{codeTaken ? ' 这个编码已经被别的场景占用。' : ''}</span>
      </label>
      <label>说明
        <textarea maxLength={256} placeholder="这个场景给谁用、收录什么（选填）" value={form.description ?? ''} onChange={e => setForm({ ...form, description: e.target.value || null })} />
      </label>
      <div className="scene-options">
        <label>
          <input type="checkbox" disabled={protectedScene} checked={form.includeAll} onChange={e => { setConfirmed(false); setForm({ ...form, includeAll: e.target.checked }) }} />
          <span className="scene-option"><span>全包含</span><span className="scene-hint">收录图库里的全部图片，不看收录规则。不勾选时，只收录类别或菜系命中规则的图片。</span></span>
        </label>
        <label>
          <input type="checkbox" checked={form.outputsHeating} onChange={e => setForm({ ...form, outputsHeating: e.target.checked })} />
          <span className="scene-option"><span>输出加热信息</span><span className="scene-hint">搜索结果附带加热组和食物状态，给一体机这类要用加热参数的场景。改动立即生效。</span></span>
        </label>
        <label>
          <input type="checkbox" disabled={protectedScene} checked={form.isActive} onChange={e => { setConfirmed(false); setForm({ ...form, isActive: e.target.checked }) }} />
          <span className="scene-option"><span>启用</span><span className="scene-hint">停用后搜图时不能选这个场景，也没有图片归属它；规则和标签保留。</span></span>
        </label>
      </div>
    </fieldset>
    {protectedScene && <p className="mg-help">这是默认场景：没有指定场景的搜图请求都用它，所以不能停用，也不能取消全包含。</p>}
    {confirmed && <div className="scene-form-note" role="alert">
      {(!scene || scene.includeAll !== form.includeAll) && <p>{form.includeAll
        ? '开启全包含后，这个场景收录图库里的全部图片，收录规则和单张图片上的人工纳入、排除不再起作用。'
        : '关闭全包含后改按收录规则收录；之前被忽略的人工纳入、排除会重新起作用。'}</p>}
      {scene?.isActive !== form.isActive && <p>{form.isActive
        ? '启用后要全量同步一次，图片才会归属到这个场景。'
        : '停用后搜图时不能选这个场景，也没有图片归属它；规则、标签和人工调整都保留，重新启用后要全量同步。'}</p>}
      <p>确定保存？保存后按提示做一次全量同步，已入库的图片才按新配置归属。</p>
    </div>}
    {invalid && <p className="mg-form-error" role="alert">{invalid}</p>}
    <div className="dialog-actions">
      <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button>
      <button type="submit" className="mg-btn mg-primary" disabled={busy}>{confirmed ? '确认保存' : changesFlags ? '下一步：确认变更' : '保存'}</button>
    </div>
  </form></Dialog>
}

export function LabelEditor({ label, busy, codeTaken = false, onClose, onSave }: { label?: SceneLabel; busy: boolean; codeTaken?: boolean; onClose: () => void; onSave: (input: LabelInput) => Promise<boolean> }) {
  const [form, setForm] = useState<LabelInput>(label ? { code: label.code, name: label.name, sortOrder: label.sortOrder, isActive: label.isActive } : { code: '', name: '', sortOrder: 0, isActive: true })
  const [invalid, setInvalid] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault(); setInvalid('')
    const code = form.code.trim()
    if (!codePattern.test(code) || code.length > 64 || !form.name.trim() || form.name.trim().length > 64 || !Number.isInteger(form.sortOrder) || form.sortOrder < -2147483648 || form.sortOrder > 2147483647) {
      setInvalid('请检查：名称和编码必填，各不超过 64 个字符；编码以小写字母开头，只含小写字母、数字和下划线；排序填整数。')
      return
    }
    if (await onSave(form)) onClose()
  }

  return <Dialog title={label ? '编辑标签' : '新建标签'} size="sm" onClose={onClose} dismissDisabled={busy}><form className="scene-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={busy}>
      <label>名称
        <input data-dialog-initial-focus required maxLength={64} placeholder="如 烤肉类" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <span className="scene-hint">搜索结果的「场景标签」里显示的文字。</span>
      </label>
      <label>编码
        <input required aria-invalid={codeTaken} maxLength={64} placeholder="如 roast_meat" value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} />
        <span className="scene-hint">在这个场景里不能重复。小写字母开头，可以有数字和下划线。{codeTaken ? ' 这个编码已经被这个场景的另一个标签占用。' : ''}</span>
      </label>
      <label>排序
        <input required type="number" step="1" min={-2147483648} max={2147483647} value={Number.isNaN(form.sortOrder) ? '' : form.sortOrder} onChange={e => setForm({ ...form, sortOrder: e.target.value === '' ? NaN : Number(e.target.value) })} />
        <span className="scene-hint">标签列表按它从小到大排。</span>
      </label>
      <label>
        <input type="checkbox" checked={form.isActive} onChange={e => setForm({ ...form, isActive: e.target.checked })} />
        <span className="scene-option"><span>启用</span><span className="scene-hint">停用的标签不在搜索结果里显示；规则里的对应关系保留。</span></span>
      </label>
    </fieldset>
    <p className="mg-help">新建标签后，到「收录规则」里把类别对应到它，搜索结果才会显示。改动标签立即生效，不需要同步。</p>
    {invalid && <p className="mg-form-error" role="alert">{invalid}</p>}
    <div className="dialog-actions">
      <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button>
      <button type="submit" className="mg-btn mg-primary" disabled={busy}>保存</button>
    </div>
  </form></Dialog>
}
