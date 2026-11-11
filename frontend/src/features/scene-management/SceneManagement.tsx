import { useEffect, useRef, useState } from 'react'
import { Dialog } from '../../components/Dialog'
import { EmptyState } from '../../shared/ui'
import { Layers3 } from 'lucide-react'
import { createRule, deleteSceneResource, sceneError, uncertainWrite, updateRule, writeLabel, writeScene, type Scene, type SceneLabel, type SceneRule, type SceneWrite } from '../../entities/scene'
import { refreshSceneCatalog, sceneSaved, useSceneCatalog } from '../scene-catalog'
import { beginSceneWrite, endSceneWrite, sceneWritesLocked, stopSceneSync, useSceneSync } from '../scene-sync'
import { SceneEditor, LabelEditor } from './Editors'
import { RuleEditor } from './RuleEditor'
import { SceneSyncPanel } from './SceneSyncPanel'
import './scene-management.css'
type Tab = 'overview' | 'labels' | 'rules' | 'sync'
type Editor = { type: 'scene'; scene?: Scene } | { type: 'label'; scene: Scene; label?: SceneLabel } | { type: 'rule'; scene: Scene; rule?: SceneRule }
const tabs: { key: Tab; label: string }[] = [{ key: 'overview', label: '概览' }, { key: 'labels', label: '标签' }, { key: 'rules', label: '规则' }, { key: 'sync', label: '同步' }]
function initial() { const p = new URLSearchParams(window.location.search); return { id: Number(p.get('scene')) || null, tab: tabs.some(t => t.key === p.get('sceneTab')) ? p.get('sceneTab') as Tab : 'overview' as Tab } }
export function SceneManagement({ apiBase }: { apiBase: string }) {
  const c = useSceneCatalog(apiBase); const sync = useSceneSync(); const [selected, setSelected] = useState<number | null>(() => initial().id); const [tab, setTab] = useState<Tab>(() => initial().tab)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [busy, setBusy] = useState(false); const pending = useRef(false)
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [trace, setTrace] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<{ title: string; text: string; action: () => Promise<boolean> } | null>(null)
  const locked = sync.running || sync.inFlight; const canWrite = Boolean(c.data && !c.error && !c.loading && !busy && !locked)
  const scene = c.data?.scenes.find(s => s.id === selected)
  useEffect(() => { void refreshSceneCatalog(apiBase, true); return () => stopSceneSync() }, [apiBase])
  useEffect(() => {
    if (selected !== null && c.data && !c.data.scenes.some(s => s.id === selected)) setSelected(c.data.scenes.find(s => s.code === c.data!.defaultScene)?.id ?? null)
  }, [c.data, selected])
  useEffect(() => { const url = new URL(window.location.href); if (selected) url.searchParams.set('scene', String(selected)); else url.searchParams.delete('scene'); url.searchParams.set('sceneTab', tab); window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`) }, [selected, tab])
  async function perform(action: () => Promise<SceneWrite>): Promise<boolean> {
    if (pending.current || sceneWritesLocked() || !c.data || c.error || c.loading) return false
    if (!beginSceneWrite()) return false
    pending.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const result = await action(); setTrace(result.traceId)
      await sceneSaved(apiBase, result.scene, result.deletedId, result.syncRequired)
      // The server says whether stored images still follow the old configuration
      setNotice(result.syncRequired ? '已保存。' : '已保存，已生效（不需要同步）。'); return true
    }
    catch (e) { setError(sceneError(e)); if (uncertainWrite(e)) { setNotice('写入结果不确定，已先刷新目录对账。不会自动重放，请核对后再决定手动操作。'); await refreshSceneCatalog(apiBase, true) }; return false }
    finally { pending.current = false; setBusy(false); endSceneWrite() }
  }
  function open(value: Editor) { setError(''); setEditor(value) }
  function ask(title: string, text: string, action: () => Promise<boolean>) { setError(''); setConfirmation({ title, text, action }) }
  const protectedScene = Boolean(scene && (scene.code === 'general' || scene.code === c.data?.defaultScene))
  return <div className="scene-management">
    <div className="mg-page-toolbar"><h2>场景管理</h2><button className="mg-btn mg-secondary" onClick={() => void c.refresh()}>刷新</button><button className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'scene' })}>新建场景</button><button className="mg-btn mg-secondary" onClick={() => setTab('sync')}>全量场景同步</button></div>
    {c.loading && <p role="status">正在加载场景目录…</p>}
    {(c.error || error) && <p className="mg-form-error" role="alert">{c.error || error}{c.error && ' 已保留上次结果，依赖目录的写入已禁用。'}</p>}
    {notice && <p role="status">{notice}</p>}
    {c.pendingSync && <p role="status">有配置改动尚未同步：已入库图片仍按旧配置归属，检索也是。<button className="text-button" onClick={() => setTab('sync')}>去同步</button></p>}
    {locked && <p role="status">全量同步正在进行，本页配置与同窗口人工成员写入已禁用。</p>}
    <div className="scene-layout"><aside className="mg-panel scene-directory" aria-label="全量场景列表">
      {c.data?.scenes.map(s => <button className={`scene-directory-item ${s.id === selected ? 'active' : ''}`} key={s.id} aria-pressed={s.id === selected} onClick={() => setSelected(s.id)}><strong>{s.name}</strong><code>{s.code}</code><small>{s.isActive ? '启用' : '停用'} · {s.includeAll ? '全包含' : '按规则'}{s.code === c.data?.defaultScene ? ' · 默认' : ''}</small><small>成员 {s.memberCount}（已物化）</small></button>)}
      {c.data && !c.data.scenes.length && <EmptyState icon={Layers3} title="暂无场景" description="目录为空，可新建场景；全量同步仍可从页头进入。" />}
    </aside><section className="mg-panel scene-details">
      <button className="text-button scene-back" onClick={() => setSelected(null)}>返回列表</button>
      {scene && <><h3>{scene.name} <code>{scene.code}</code></h3><p>{scene.isActive ? '启用' : '停用'} · {scene.includeAll ? '全包含' : '按规则'} {scene.code === c.data?.defaultScene ? '· 默认（只读）' : ''}</p></>}
      {(scene || tab === 'sync') && <div className="mg-tabs" role="tablist" aria-label="场景详情">{tabs.map(t => <button key={t.key} role="tab" aria-selected={tab === t.key} aria-controls={`scene-tab-${t.key}`} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>{t.label}</button>)}</div>}
      {!scene && tab !== 'sync' && <p>请选择左侧场景</p>}
      {tab === 'sync' && <div role="tabpanel" id="scene-tab-sync"><SceneSyncPanel apiBase={apiBase} configBusy={busy} /></div>}
      {scene && tab === 'overview' && <div role="tabpanel" id="scene-tab-overview"><dl className="detail-fields"><div><dt>描述</dt><dd>{scene.description || '—'}</dd></div><div><dt>输出加热信息</dt><dd>{scene.outputsHeating ? '是' : '否'}</dd></div><div><dt>创建时间</dt><dd>{scene.createdAt ? new Date(scene.createdAt).toLocaleString('zh-CN', { timeZone: 'America/Denver' }) + ' MST' : '—'}</dd></div><div><dt>有效成员数</dt><dd>{scene.memberCount}（当前已物化值，不是新规则实时预览）</dd></div></dl>
        <p>全包含会忽略规则与人工覆盖；关闭后旧人工覆盖可能恢复。多个全包含场景不等于默认。</p>
        {protectedScene && <p>默认场景不可停用、删除或取消全包含；名称、描述和加热标志仍可编辑。</p>}
        <div className="scene-actions"><button className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'scene', scene })}>编辑</button>
          <button className="mg-btn mg-secondary" disabled={!canWrite || protectedScene} onClick={() => ask(scene.isActive ? '停用场景' : '启用场景', scene.isActive ? '停用后无有效成员且不可检索，规则与人工覆盖保留。恢复后需显式同步。' : '启用后需显式全量同步，检索索引才会一致。', () => perform(() => writeScene(apiBase, { ...scene, isActive: !scene.isActive }, scene.id)))}>{scene.isActive ? '停用' : '启用'}</button>
          <button className="mg-btn mg-secondary" disabled={!canWrite || protectedScene} onClick={() => ask('删除场景', `删除「${scene.name}」将级联删除 labels、rules 和图片归属 memberships，不删除图片本身。Qdrant 旧 code 待显式全量同步才会清除。不可恢复。`, () => perform(() => deleteSceneResource(apiBase, scene.id)))}>删除</button></div>
      </div>}
      {scene && tab === 'labels' && <div role="tabpanel" id="scene-tab-labels"><button className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'label', scene })}>新建标签</button><div className="scene-table-scroll"><table className="scene-table"><thead><tr><th>code</th><th>名称</th><th>排序</th><th>状态</th><th>引用数</th><th>操作</th></tr></thead><tbody>{scene.labels.map(l => <tr key={l.id}><td>{l.code}</td><td>{l.name}</td><td>{l.sortOrder}</td><td>{l.isActive ? '启用' : '停用'}</td><td>{l.ruleCount}</td><td><button className="text-button" disabled={!canWrite} onClick={() => open({ type: 'label', scene, label: l })}>编辑</button><button className="text-button" disabled={!canWrite} onClick={() => ask(l.isActive ? '停用标签' : '启用标签', `${l.isActive ? '停用后检索结果不再输出它，命中改按更上层的规则取标签' : '启用后检索结果重新输出它'}；立即生效，不需要同步。已有的规则映射保留。`, () => perform(() => writeLabel(apiBase, scene.id, { ...l, isActive: !l.isActive }, l.id)))}>{l.isActive ? '停用' : '启用'}</button><button className="text-button" disabled={!canWrite} onClick={() => ask('删除标签', '仍被任何规则引用（含停用标签）时不能删除，服务器会返回 SCENE_IN_USE。', () => perform(() => deleteSceneResource(apiBase, scene.id, 'labels', l.id)))}>删除</button></td></tr>)}</tbody></table></div></div>}
      {scene && tab === 'rules' && <div role="tabpanel" id="scene-tab-rules"><button className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'rule', scene })}>新建规则</button><div className="scene-table-scroll"><table className="scene-table"><thead><tr><th>目标</th><th>映射标签</th><th>操作</th></tr></thead><tbody>{scene.rules.map(r => <tr key={r.id}><td>{r.category ? `${r.category.name ?? r.category.code}（${r.category.code}）` : r.cuisine}</td><td>{r.label ? `${r.label.name ?? r.label.code}（${r.label.code}）` : '无'}</td><td><button className="text-button" disabled={!canWrite} onClick={() => open({ type: 'rule', scene, rule: r })}>编辑</button><button className="text-button" disabled={!canWrite} onClick={() => ask('删除规则', '删除规则不会清除已有的人工调整；已入库图片要同步后才按剩下的规则归属。', () => perform(() => deleteSceneResource(apiBase, scene.id, 'rules', r.id)))}>删除</button></td></tr>)}</tbody></table></div></div>}
    </section></div><small>{trace || c.data?.traceId ? `trace_id=${trace ?? c.data?.traceId}` : ''}</small>
    {editor?.type === 'scene' && <SceneEditor scene={editor.scene} protectedScene={Boolean(editor.scene && (editor.scene.code === 'general' || editor.scene.code === c.data?.defaultScene))} busy={busy || locked} error={error} onClose={() => setEditor(null)} onSave={input => perform(() => writeScene(apiBase, input, editor.scene?.id))} />}
    {editor?.type === 'label' && <LabelEditor label={editor.label} busy={busy || locked} error={error} onClose={() => setEditor(null)} onSave={input => perform(() => writeLabel(apiBase, editor.scene.id, input, editor.label?.id))} />}
    {editor?.type === 'rule' && <RuleEditor scene={editor.scene} rule={editor.rule} apiBase={apiBase} busy={busy || locked} error={error} onClose={() => setEditor(null)} onSave={input => perform(() => editor.rule
      ? updateRule(apiBase, editor.scene.id, editor.rule.id, input)
      : createRule(apiBase, editor.scene.id, input))} />}
    {confirmation && <Dialog title={confirmation.title} size="sm" onClose={() => setConfirmation(null)} dismissDisabled={busy}><p>{confirmation.text}</p>{error && <p className="mg-form-error" role="alert">{error}</p>}<button className="mg-btn mg-secondary" disabled={busy} onClick={() => setConfirmation(null)}>取消</button><button className="mg-btn mg-primary" disabled={!canWrite} onClick={async () => { if (await confirmation.action()) setConfirmation(null) }}>确认</button></Dialog>}
  </div>
}
