import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Dialog } from '../../components/Dialog'
import { EmptyState, notify } from '../../shared/ui'
import { ApiError } from '../../shared/api'
import { Layers3, LoaderCircle } from 'lucide-react'
import { createRule, deleteSceneResource, sceneError, uncertainWrite, updateRule, writeLabel, writeScene, type Scene, type SceneLabel, type SceneRule, type SceneWrite } from '../../entities/scene'
import { refreshSceneCatalog, sceneSaved, useSceneCatalog } from '../scene-catalog'
import { beginSceneWrite, endSceneWrite, sceneWritesLocked, stopSceneSync, useSceneSync } from '../scene-sync'
import { SceneEditor, LabelEditor } from './Editors'
import { RuleEditor } from './RuleEditor'
import { SceneSyncPanel } from './SceneSyncPanel'
import './scene-management.css'
type Tab = 'overview' | 'labels' | 'rules' | 'sync'
type Editor = { type: 'scene'; scene?: Scene } | { type: 'label'; scene: Scene; label?: SceneLabel } | { type: 'rule'; scene: Scene; rule?: SceneRule }
const tabs: { key: Tab; label: string }[] = [{ key: 'overview', label: '概览' }, { key: 'labels', label: '标签' }, { key: 'rules', label: '规则' }, { key: 'sync', label: '全量同步' }]
function initial() { const p = new URLSearchParams(window.location.search); return { id: Number(p.get('scene')) || null, tab: tabs.some(t => t.key === p.get('sceneTab')) ? p.get('sceneTab') as Tab : 'overview' as Tab } }
export function SceneManagement({ apiBase }: { apiBase: string }) {
  const c = useSceneCatalog(apiBase); const sync = useSceneSync(); const [selected, setSelected] = useState<number | null>(() => initial().id); const [tab, setTab] = useState<Tab>(() => initial().tab)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [busy, setBusy] = useState(false); const pending = useRef(false)
  /** The last write was refused for a code already taken: the editor marks its code field. */
  const [codeTaken, setCodeTaken] = useState(false)
  const [confirmation, setConfirmation] = useState<{ title: string; text: string; action: () => Promise<boolean> } | null>(null)
  const detailsRef = useRef<HTMLElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const headingRef = useRef<HTMLElement>(null)
  const tabsRef = useRef<HTMLDivElement>(null)
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
    pending.current = true; setBusy(true); setCodeTaken(false)
    try {
      const result = await action()
      await sceneSaved(apiBase, result.scene, result.deletedId, result.syncRequired)
      // The server says whether stored images still follow the old configuration
      if (result.syncRequired) notify.success('已保存。已入库图片要全量同步后才按新配置归属。', { action: { label: '去同步', onClick: () => setTab('sync') } })
      else notify.success('已保存，已生效（不需要同步）。')
      return true
    }
    catch (e) {
      const reason = sceneError(e)
      setCodeTaken(reason.includes('SCENE_CODE_EXISTS') || (e instanceof ApiError && e.code === 'SCENE_CODE_EXISTS'))
      if (uncertainWrite(e)) {
        notify.warn(`${reason}
写入结果不确定，已先刷新目录对账。不会自动重放，请核对后再决定手动操作。`, { title: '写入结果不确定' })
        await refreshSceneCatalog(apiBase, true)
      } else notify.error(reason)
      return false
    }
    finally { pending.current = false; setBusy(false); endSceneWrite() }
  }
  function open(value: Editor) { setCodeTaken(false); setEditor(value) }
  function ask(title: string, text: string, action: () => Promise<boolean>) { setConfirmation({ title, text, action }) }
  const protectedScene = Boolean(scene && (scene.code === 'general' || scene.code === c.data?.defaultScene))
  useLayoutEffect(() => {
    const details = detailsRef.current; const scroll = scrollRef.current
    if (!details || !scroll) return
    const measure = () => {
      const headingHeight = headingRef.current?.getBoundingClientRect().height ?? 0
      const tabsHeight = tabsRef.current?.getBoundingClientRect().height ?? 0
      scroll.style.setProperty('--scene-heading-height', `${headingHeight}px`)
      scroll.style.setProperty('--scene-tabs-height', `${tabsHeight}px`)
      const style = getComputedStyle(details)
      const width = details.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      details.dataset.sceneWidth = width < 480 ? 'compact' : width < 720 ? 'medium' : 'wide'
      details.dataset.stickyHeading = headingHeight + tabsHeight > scroll.clientHeight * 0.4 ? 'false' : 'true'
    }
    const observer = new ResizeObserver(measure)
    observer.observe(details); observer.observe(scroll)
    if (headingRef.current) observer.observe(headingRef.current)
    if (tabsRef.current) observer.observe(tabsRef.current)
    measure()
    return () => observer.disconnect()
  }, [selected, tab, Boolean(scene)])
  useLayoutEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0 }, [selected, tab])
  return <div className="scene-management">
    <div className="mg-page-toolbar"><h2>场景管理</h2><button className="mg-btn mg-secondary" onClick={() => void c.refresh()}>刷新</button><button className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'scene' })}>新建场景</button><button className="mg-btn mg-secondary" onClick={() => setTab('sync')}>全量同步</button></div>
    <div className="scene-status-stack scene-directory-status" aria-label="场景目录状态">
      {c.error && <p className="mg-form-error" role="alert">{c.error} 已保留上次结果，依赖目录的写入已禁用。<button type="button" className="text-button" onClick={() => void c.refresh()}>重试刷新</button></p>}
      {locked && <p role="status">全量同步正在进行，本页配置与同窗口人工成员写入已禁用。</p>}
      {c.pendingSync && <p role="status">有配置改动尚未同步：已入库图片仍按旧配置归属，检索也是。<button type="button" className="text-button" onClick={() => setTab('sync')}>去同步</button></p>}
      {c.loading && <p className="scene-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden />正在加载场景目录…</p>}
    </div>
    {/* Narrow screens show one pane at a time: the list, or what was picked from it (a scene, or the full sync) */}
    <div className="scene-layout" data-pane={scene || tab === 'sync' ? 'detail' : 'list'}><aside className="mg-panel scene-directory" aria-label="全量场景列表">
      {c.data?.scenes.map(s => <button className={`scene-directory-item ${s.id === selected ? 'active' : ''}`} key={s.id} aria-pressed={s.id === selected} onClick={() => setSelected(s.id)}><strong>{s.name}</strong><code>{s.code}</code><small>{s.isActive ? '启用' : '停用'} · {s.includeAll ? '全包含' : '按规则'}{s.code === c.data?.defaultScene ? ' · 默认' : ''}</small><small>成员 {s.memberCount}（已物化）</small></button>)}
      {c.data && !c.data.scenes.length && <EmptyState icon={Layers3} title="暂无场景" description="目录为空，可新建场景；全量同步仍可从页头进入。" />}
    </aside><section className="mg-panel scene-details" ref={detailsRef} aria-label="场景详情">
      <div className="scene-detail-scroll" ref={scrollRef} onFocusCapture={event => {
        const target = event.target as HTMLElement; const scroll = scrollRef.current
        if (!scroll || target.closest('dialog') || !scroll.contains(target)) return
        const top = scroll.getBoundingClientRect().top + parseFloat(getComputedStyle(scroll).scrollPaddingTop || '0')
        if (target.getBoundingClientRect().top < top) scroll.scrollBy({ top: target.getBoundingClientRect().top - top, behavior: 'auto' })
      }}>
        <div className="scene-status-stack scene-scoped-status" aria-label="当前场景状态">
          {protectedScene && !editor && !confirmation && <p id="scene-protection-note">默认场景不可停用、删除或取消全包含；名称、描述和加热标志仍可编辑。</p>}
        </div>
        {(scene || tab === 'sync') && <button type="button" className="text-button scene-back" onClick={() => { setSelected(null); if (tab === 'sync') setTab('overview') }}>返回列表</button>}
        {(scene || tab === 'sync') && <header className="scene-detail-header" ref={headingRef}>
          <div className="scene-heading-text">
            <h3 className="scene-detail-name">{scene ? scene.name : '全量同步'}</h3>
            {scene && <><code className="scene-detail-code">{scene.code}</code><div className="scene-detail-badges"><span className="scene-detail-badge">{scene.isActive ? '启用' : '停用'}</span>{scene.code === c.data?.defaultScene && <span className="scene-detail-badge">默认（只读）</span>}{scene.includeAll && <span className="scene-detail-badge">全包含</span>}</div></>}
          </div>
          {scene && <div className="scene-detail-actions"><div className="scene-detail-primary-actions">
            <button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'scene', scene })}>编辑</button>
            <button type="button" className="mg-btn mg-secondary" disabled={!canWrite || protectedScene} aria-describedby={protectedScene ? 'scene-protection-note' : undefined} onClick={() => ask(scene.isActive ? '停用场景' : '启用场景', scene.isActive ? '停用后无有效成员且不可检索，规则与人工覆盖保留。恢复后需显式同步。' : '启用后需显式全量同步，检索索引才会一致。', () => perform(() => writeScene(apiBase, { ...scene, isActive: !scene.isActive }, scene.id)))}>{scene.isActive ? '停用' : '启用'}</button>
          </div><div className="scene-detail-danger-actions"><button type="button" className="mg-btn mg-secondary" disabled={!canWrite || protectedScene} aria-describedby={protectedScene ? 'scene-protection-note' : undefined} onClick={() => ask('删除场景', `删除「${scene.name}」将级联删除 labels、rules 和图片归属 memberships，不删除图片本身。Qdrant 旧 code 待显式全量同步才会清除。不可恢复。`, () => perform(() => deleteSceneResource(apiBase, scene.id)))}>删除</button></div></div>}
        </header>}
        {(scene || tab === 'sync') && <div className="mg-tabs scene-detail-tabs" ref={tabsRef} role="tablist" aria-label="场景详情" onKeyDown={event => {
          const index = tabs.findIndex(t => t.key === tab)
          const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
          if (next === null) return
          event.preventDefault(); setTab(tabs[next].key); tabsRef.current?.querySelectorAll<HTMLButtonElement>('[role=tab]')[next]?.focus()
        }}>{tabs.map(t => <button type="button" key={t.key} id={`scene-detail-tab-${t.key}`} role="tab" tabIndex={tab === t.key ? 0 : -1} aria-selected={tab === t.key} aria-controls={`scene-tab-${t.key}`} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>{t.label}</button>)}</div>}
        {!scene && tab !== 'sync' && <EmptyState icon={Layers3} title="请选择左侧场景" description="查看场景概览、标签与规则；全量同步也可从页头直接进入。" />}
        {tab === 'sync' && <div className="scene-tab-body" role="tabpanel" id="scene-tab-sync" aria-labelledby="scene-detail-tab-sync"><SceneSyncPanel apiBase={apiBase} configBusy={busy} /></div>}
        {scene && tab === 'overview' && <div className="scene-tab-body scene-overview" role="tabpanel" id="scene-tab-overview" aria-labelledby="scene-detail-tab-overview">
          <section className="scene-overview-group"><h4>说明</h4><p className="scene-description">{scene.description || '—'}</p></section>
          <section className="scene-overview-group"><h4>成员</h4><p className="scene-member-count">{scene.memberCount}</p><p className="scene-detail-caption">当前已物化值，不是新规则实时预览</p></section>
          <section className="scene-overview-group"><div className="scene-group-heading"><h4>属性摘要</h4><button type="button" className="text-button" disabled={!canWrite} onClick={() => open({ type: 'scene', scene })}>编辑</button></div>
            <dl className="scene-summary"><div><dt>全包含</dt><dd>{scene.includeAll ? '是' : '否'}</dd></div><div><dt>输出加热信息</dt><dd>{scene.outputsHeating ? '是' : '否'}</dd></div><div><dt>启用</dt><dd>{scene.isActive ? '是' : '否'}</dd></div><div><dt>创建时间</dt><dd>{scene.createdAt ? new Date(scene.createdAt).toLocaleString('zh-CN', { timeZone: 'America/Denver' }) + ' MST' : '—'}</dd></div></dl>
            <p className="scene-detail-caption">全包含会忽略规则与人工覆盖；关闭后旧人工覆盖可能恢复。多个全包含场景不等于默认。</p>
          </section>
        </div>}
        {scene && tab === 'labels' && <div className="scene-tab-body" role="tabpanel" id="scene-tab-labels" aria-labelledby="scene-detail-tab-labels">
          <div className="scene-tab-toolbar"><h4>标签</h4><button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'label', scene })}>新建标签</button></div>
          {!scene.labels.length ? <div className="scene-tab-empty"><EmptyState icon={Layers3} title="暂无标签" description="通过工具栏新建本场景标签。" /></div> : <div className="scene-table-scroll"><table className="scene-table"><thead><tr><th>code</th><th>名称</th><th>排序</th><th>状态</th><th>引用数</th><th>操作</th></tr></thead><tbody>{scene.labels.map(l => <tr key={l.id}><td>{l.code}</td><td>{l.name}</td><td>{l.sortOrder}</td><td>{l.isActive ? '启用' : '停用'}</td><td>{l.ruleCount}</td><td><div className="scene-table-actions"><button type="button" className="text-button" disabled={!canWrite} onClick={() => open({ type: 'label', scene, label: l })}>编辑</button><button type="button" className="text-button" disabled={!canWrite} onClick={() => ask(l.isActive ? '停用标签' : '启用标签', `${l.isActive ? '停用后检索结果不再输出它，命中改按更上层的规则取标签' : '启用后检索结果重新输出它'}；立即生效，不需要同步。已有的规则映射保留。`, () => perform(() => writeLabel(apiBase, scene.id, { ...l, isActive: !l.isActive }, l.id)))}>{l.isActive ? '停用' : '启用'}</button><button type="button" className="text-button" disabled={!canWrite} onClick={() => ask('删除标签', '仍被任何规则引用（含停用标签）时不能删除，服务器会返回 SCENE_IN_USE。', () => perform(() => deleteSceneResource(apiBase, scene.id, 'labels', l.id)))}>删除</button></div></td></tr>)}</tbody></table></div>}
        </div>}
        {scene && tab === 'rules' && <div className="scene-tab-body" role="tabpanel" id="scene-tab-rules" aria-labelledby="scene-detail-tab-rules">
          <div className="scene-tab-toolbar"><h4>规则</h4><button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={() => open({ type: 'rule', scene })}>新建规则</button></div>
          {!scene.rules.length ? <div className="scene-tab-empty"><EmptyState icon={Layers3} title="暂无规则" description="通过工具栏新建类别或菜系规则。" /></div> : <div className="scene-table-scroll"><table className="scene-table"><thead><tr><th>目标</th><th>映射标签</th><th>操作</th></tr></thead><tbody>{scene.rules.map(r => <tr key={r.id}><td>{r.category ? `${r.category.name ?? r.category.code}（${r.category.code}）` : r.cuisine}</td><td>{r.label ? `${r.label.name ?? r.label.code}（${r.label.code}）` : '无'}</td><td><div className="scene-table-actions"><button type="button" className="text-button" disabled={!canWrite} onClick={() => open({ type: 'rule', scene, rule: r })}>编辑</button><button type="button" className="text-button" disabled={!canWrite} onClick={() => ask('删除规则', '删除规则不会清除已有的人工调整；已入库图片要同步后才按剩下的规则归属。', () => perform(() => deleteSceneResource(apiBase, scene.id, 'rules', r.id)))}>删除</button></div></td></tr>)}</tbody></table></div>}
        </div>}
        <small className="scene-detail-trace">{c.data?.traceId ? `trace_id=${c.data.traceId}` : ''}</small>
      </div>
    </section></div>
    {editor?.type === 'scene' && <SceneEditor scene={editor.scene} protectedScene={Boolean(editor.scene && (editor.scene.code === 'general' || editor.scene.code === c.data?.defaultScene))} busy={busy || locked} codeTaken={codeTaken} onClose={() => setEditor(null)} onSave={input => perform(() => writeScene(apiBase, input, editor.scene?.id))} />}
    {editor?.type === 'label' && <LabelEditor label={editor.label} busy={busy || locked} codeTaken={codeTaken} onClose={() => setEditor(null)} onSave={input => perform(() => writeLabel(apiBase, editor.scene.id, input, editor.label?.id))} />}
    {editor?.type === 'rule' && <RuleEditor scene={editor.scene} rule={editor.rule} apiBase={apiBase} busy={busy || locked} onClose={() => setEditor(null)} onSave={input => perform(() => editor.rule
      ? updateRule(apiBase, editor.scene.id, editor.rule.id, input)
      : createRule(apiBase, editor.scene.id, input))} />}
    {confirmation && <Dialog title={confirmation.title} size="sm" onClose={() => setConfirmation(null)} dismissDisabled={busy}><p>{confirmation.text}</p><button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={() => setConfirmation(null)}>取消</button><button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={async () => { if (await confirmation.action()) setConfirmation(null) }}>确认</button></Dialog>}
  </div>
}