import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft, CircleAlert, Download, LoaderCircle, RefreshCw, TriangleAlert, Upload, UploadCloud } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { notify } from '../../shared/ui'
import { ApiError } from '../../shared/api'
import {
  createRule, deleteSceneResource, sceneError, uncertainWrite, updateRule, writeLabel, writeScene,
  type Scene, type SceneLabel, type SceneRule, type SceneWrite,
} from '../../entities/scene'
import { readJsonFile, saveText } from '../../shared/lib/saveFile'
import { ImportDialog, importSceneConfig, type ImportReport } from '../config-transfer'
import { markScenePending, refreshSceneCatalog, sceneSaved, useSceneCatalog } from '../scene-catalog'
import { beginSceneWrite, endSceneWrite, sceneWritesLocked, stopSceneSync, useSceneSync } from '../scene-sync'
import { buildSceneConfigExport } from './export'
import { LabelEditor, SceneEditor } from './Editors'
import { RuleEditor } from './RuleEditor'
import { SceneDetail, type SceneTab } from './SceneDetail'
import { SceneDirectory } from './SceneDirectory'
import { SceneGuide } from './SceneGuide'
import { SceneSyncPanel } from './SceneSyncPanel'
import { countText, libraryImageCount } from './summary'
import './scene-management.css'

/** What the right-hand side shows: a scene (or the guide when none is picked), or the full sync, which is about every scene. */
type View = 'scene' | 'sync'
type Editor = { type: 'scene'; scene?: Scene } | { type: 'label'; scene: Scene; label?: SceneLabel } | { type: 'rule'; scene: Scene; rule?: SceneRule }
interface Confirmation {
  title: string
  text: string
  /** What the button does, in its own words (「删除场景」, not 「确认」). */
  confirmLabel: string
  danger?: boolean
  action: () => Promise<boolean>
}

/** ?scene=<id>&sceneTab=rules|labels|sync: other pages open the full sync with sceneTab=sync. */
function initial(): { id: number | null; view: View; tab: SceneTab } {
  const params = new URLSearchParams(window.location.search)
  const requested = params.get('sceneTab')
  return {
    id: Number(params.get('scene')) || null,
    view: requested === 'sync' ? 'sync' : 'scene',
    tab: requested === 'labels' ? 'labels' : 'rules',
  }
}

export function SceneManagement({ apiBase }: { apiBase: string }) {
  const c = useSceneCatalog(apiBase)
  const sync = useSceneSync()
  const [start] = useState(initial)
  const [selected, setSelected] = useState<number | null>(start.id)
  const [view, setView] = useState<View>(start.view)
  const [tab, setTab] = useState<SceneTab>(start.tab)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  /** The last write was refused for a code already taken: the editor marks its code field. */
  const [codeTaken, setCodeTaken] = useState(false)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  /** A file picked for 「上传配置」, parsed: its preview and import run in the dialog. */
  const [importing, setImporting] = useState<{ name: string; document: unknown } | null>(null)
  const importInput = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const locked = sync.running || sync.inFlight
  const canWrite = Boolean(c.data && !c.error && !c.loading && !busy && !locked)
  const scenes = c.data?.scenes ?? null
  const defaultScene = c.data?.defaultScene ?? null
  const scene = scenes?.find(s => s.id === selected)
  const libraryTotal = libraryImageCount(c.data)
  const isProtected = (target: Scene) => target.code === 'general' || target.code === defaultScene

  useEffect(() => { void refreshSceneCatalog(apiBase, true); return () => stopSceneSync() }, [apiBase])
  // A scene that is gone (deleted here or elsewhere) falls back to the default one
  useEffect(() => {
    if (selected !== null && c.data && !c.data.scenes.some(s => s.id === selected)) {
      setSelected(c.data.scenes.find(s => s.code === c.data!.defaultScene)?.id ?? null)
    }
  }, [c.data, selected])
  useEffect(() => {
    const url = new URL(window.location.href)
    if (selected) url.searchParams.set('scene', String(selected)); else url.searchParams.delete('scene')
    url.searchParams.set('sceneTab', view === 'sync' ? 'sync' : tab)
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }, [selected, tab, view])
  useLayoutEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0 }, [selected, tab, view])

  /** One configuration write: saved → the catalog is read again and the toast says whether stored images still need the full sync. */
  async function perform(action: () => Promise<SceneWrite>, saved?: (result: SceneWrite) => void): Promise<boolean> {
    if (pending.current || sceneWritesLocked() || !c.data || c.error || c.loading) return false
    if (!beginSceneWrite()) return false
    pending.current = true; setBusy(true); setCodeTaken(false)
    try {
      const result = await action()
      await sceneSaved(apiBase, result.scene, result.deletedId, result.syncRequired)
      saved?.(result)
      // The server says whether stored images still follow the old configuration
      if (result.syncRequired) notify.success('已保存。已入库图片要全量同步后才按新配置归属。', { action: { label: '去同步', onClick: () => setView('sync') } })
      else notify.success('已保存，已生效（不需要同步）。')
      return true
    } catch (e) {
      const reason = sceneError(e)
      setCodeTaken(reason.includes('SCENE_CODE_EXISTS') || (e instanceof ApiError && e.code === 'SCENE_CODE_EXISTS'))
      if (uncertainWrite(e)) {
        notify.warn(`${reason}\n写入结果不确定，已先刷新目录对账。不会自动重放，请核对后再决定手动操作。`, { title: '写入结果不确定' })
        await refreshSceneCatalog(apiBase, true)
      } else notify.error(reason)
      return false
    } finally { pending.current = false; setBusy(false); endSceneWrite() }
  }

  /** 「下载配置」: every scene with its labels and rules, as one JSON file (also a backup). */
  function downloadConfig() {
    if (!c.data) return
    try {
      const file = buildSceneConfigExport(c.data)
      saveText(file.content, file.filename, 'application/json;charset=utf-8')
      notify.success(`已发起场景配置下载，共 ${file.count} 个场景。`)
    } catch { notify.error('场景配置文件生成失败，请重试。', { title: '下载失败' }) }
  }
  /** 「上传配置」: read the picked file here; what it would change is previewed in the dialog. */
  async function pickImport(file: File | undefined) {
    if (!file) return
    try { setImporting({ name: file.name, document: await readJsonFile(file) }) }
    catch (e) { notify.error(e instanceof Error ? e.message : '文件读取失败，请重试。', { title: '不能用这个文件' }) }
  }
  /** A preview only reads; the import itself is a configuration write, so it takes the write lock. */
  async function runImport(content: unknown, dryRun: boolean, prune: boolean): Promise<ImportReport> {
    if (dryRun) return importSceneConfig(content, { baseUrl: apiBase, dryRun: true, prune })
    if (pending.current || sceneWritesLocked() || !beginSceneWrite()) throw new Error('全量同步或另一项写入正在进行，请稍后再导入。')
    pending.current = true; setBusy(true)
    try { return await importSceneConfig(content, { baseUrl: apiBase, dryRun: false, prune }) }
    finally { pending.current = false; setBusy(false); endSceneWrite() }
  }
  /** After an import was sent: the catalog is read again; stored images wait for the full sync when the server says so (or when the outcome is unknown). */
  function importApplied(report: ImportReport | null) {
    if (!report || report.syncRequired) markScenePending(apiBase, true)
    void refreshSceneCatalog(apiBase, true)
  }

  function open(value: Editor) { setCodeTaken(false); setEditor(value) }
  function pick(id: number) { setSelected(id); setView('scene') }

  function toggleScene(target: Scene) {
    setConfirmation({
      title: target.isActive ? '停用场景' : '启用场景',
      text: target.isActive
        ? `停用「${target.name}」后，搜图时不能再选它，也不再有图片归属它。规则、标签和单张图片上的人工调整都保留；重新启用后要全量同步才恢复。`
        : `启用「${target.name}」后，要全量同步一次，图片才会按规则归属到它，搜图才有结果。`,
      confirmLabel: target.isActive ? '停用场景' : '启用场景',
      action: () => perform(() => writeScene(apiBase, { ...target, isActive: !target.isActive }, target.id)),
    })
  }
  function deleteScene(target: Scene) {
    setConfirmation({
      title: '删除场景',
      text: `删除「${target.name}」会一并删除它的 ${target.labels.length} 个场景标签、${target.rules.length} 条收录规则和全部图片归属记录，图片本身不会删除。删除后不能恢复；检索里残留的旧归属要全量同步后才清除。`,
      confirmLabel: '删除场景',
      danger: true,
      action: () => perform(() => deleteSceneResource(apiBase, target.id)),
    })
  }
  function toggleLabel(target: Scene, label: SceneLabel) {
    setConfirmation({
      title: label.isActive ? '停用标签' : '启用标签',
      text: label.isActive
        ? `停用「${label.name}」后，搜索结果不再显示它，原来显示它的图片改用更上层规则的标签。立即生效，不需要同步；规则里的对应关系保留。`
        : `启用「${label.name}」后，搜索结果重新显示它。立即生效，不需要同步。`,
      confirmLabel: label.isActive ? '停用标签' : '启用标签',
      action: () => perform(() => writeLabel(apiBase, target.id, { ...label, isActive: !label.isActive }, label.id)),
    })
  }
  function deleteLabel(target: Scene, label: SceneLabel) {
    setConfirmation({
      title: '删除标签',
      text: label.ruleCount > 0
        ? `还有 ${label.ruleCount} 条规则在用「${label.name}」，现在删除会被拒绝。请先把这些规则改成别的标签，或改成不指定标签。`
        : `删除「${label.name}」后不能恢复。现在没有规则用它，搜索结果不受影响。`,
      confirmLabel: '删除标签',
      danger: true,
      action: () => perform(() => deleteSceneResource(apiBase, target.id, 'labels', label.id)),
    })
  }
  function deleteRule(target: Scene, rule: SceneRule) {
    const what = rule.category ? `类别「${rule.category.name ?? rule.category.code}」` : `菜系「${rule.cuisine}」`
    setConfirmation({
      title: '删除规则',
      text: !target.isActive
        ? `删除${what}这条规则。场景已停用，现在不影响任何图片。`
        : target.includeAll
          ? `删除${what}这条规则。这是全包含场景，收录不受影响，只是它覆盖的类别不再对应到场景标签。`
          : `删除${what}这条规则后，只靠它收录的图片会在全量同步后移出这个场景；单张图片上的人工纳入、排除不受影响。`,
      confirmLabel: '删除规则',
      danger: true,
      action: () => perform(() => deleteSceneResource(apiBase, target.id, 'rules', rule.id)),
    })
  }

  return <div className="scene-management">
    <div className="scene-page-head">
      <p className="scene-lead">场景是搜图时可选的范围：每个场景收录图库里的一部分图片，并决定搜索结果上显示的标签。</p>
      <div className="scene-page-actions">
        <button type="button" className="mg-btn mg-secondary" aria-pressed={view === 'sync'} title="按当前配置重新计算全部已入库图片的场景归属，并写进向量库" onClick={() => setView('sync')}><UploadCloud size={16} />全量同步</button>
        <button type="button" className="mg-btn mg-secondary" disabled={!c.data || Boolean(c.error) || c.loading} title="下载全部场景及其场景标签、收录规则（JSON）：可以编辑后再上传，也可以作为备份" onClick={downloadConfig}><Download size={16} />下载配置</button>
        <button type="button" className="mg-btn mg-secondary" disabled={!canWrite} title="上传场景配置文件（下载得到的 JSON，可以改过）：按编码新建或更新场景、场景标签和收录规则，先预览再确认" onClick={() => importInput.current?.click()}><Upload size={16} />上传配置</button>
        <button type="button" className="mg-btn mg-secondary" disabled={c.loading} onClick={() => void c.refresh()}><RefreshCw size={15} />刷新</button>
        <input ref={importInput} type="file" accept=".json,application/json" hidden aria-label="选择场景配置文件" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void pickImport(file) }} />
      </div>
    </div>

    <div className="scene-callouts" aria-label="场景目录状态">
      {c.error && <p className="scene-callout is-error" role="alert">
        <CircleAlert size={16} aria-hidden />
        <span>{c.error} 下面是上次读到的内容，读到最新内容之前不能修改。</span>
        <button type="button" className="text-button" onClick={() => void c.refresh()}>重试</button>
      </p>}
      {locked && <p className="scene-callout is-info" role="status">
        <LoaderCircle size={16} className="spin" aria-hidden />
        <span>全量同步正在进行，已检查 {countText(sync.processed)} 张。同步期间不能修改场景配置，也不能调整图片的场景归属。</span>
        {view !== 'sync' && <button type="button" className="text-button" onClick={() => setView('sync')}>查看进度</button>}
      </p>}
      {c.pendingSync && !locked && <p className="scene-callout is-warn" role="status">
        <TriangleAlert size={16} aria-hidden />
        <span>有配置改动还没有同步：已入库的图片和搜图结果仍按旧配置。</span>
        {view !== 'sync' && <button type="button" className="text-button" onClick={() => setView('sync')}>去同步</button>}
      </p>}
    </div>

    {/* Narrow screens show one pane at a time: the list, or what was picked from it (a scene, or the full sync) */}
    <div className="scene-layout" data-pane={scene || view === 'sync' ? 'detail' : 'list'}>
      <SceneDirectory
        scenes={scenes}
        defaultScene={defaultScene}
        libraryTotal={libraryTotal}
        selectedId={view === 'sync' ? null : selected}
        loading={c.loading}
        canWrite={canWrite}
        onSelect={pick}
        onCreate={() => open({ type: 'scene' })}
      />
      <section className="mg-panel scene-detail" aria-label="场景详情">
        <div className="scene-detail-scroll" ref={scrollRef}>
          {(scene || view === 'sync') && <button type="button" className="text-button scene-back" onClick={() => { setSelected(null); setView('scene') }}><ArrowLeft size={14} />返回列表</button>}
          {view === 'sync'
            ? <>
              <button type="button" className="text-button scene-sync-close" onClick={() => setView('scene')}><ArrowLeft size={14} />返回场景配置</button>
              <SceneSyncPanel apiBase={apiBase} configBusy={busy} />
            </>
            : scene
              ? <SceneDetail
                scene={scene}
                isDefault={scene.code === defaultScene}
                protectedScene={isProtected(scene)}
                libraryTotal={libraryTotal}
                pendingSync={c.pendingSync}
                canWrite={canWrite}
                tab={tab}
                onTab={setTab}
                onEdit={() => open({ type: 'scene', scene })}
                onToggleActive={() => toggleScene(scene)}
                onDelete={() => deleteScene(scene)}
                onNewRule={() => open({ type: 'rule', scene })}
                onEditRule={rule => open({ type: 'rule', scene, rule })}
                onDeleteRule={rule => deleteRule(scene, rule)}
                onNewLabel={() => open({ type: 'label', scene })}
                onEditLabel={label => open({ type: 'label', scene, label })}
                onToggleLabel={label => toggleLabel(scene, label)}
                onDeleteLabel={label => deleteLabel(scene, label)}
              />
              : <SceneGuide scenes={scenes} defaultScene={defaultScene} libraryTotal={libraryTotal} />}
          {c.data?.traceId && <small className="scene-trace">trace_id={c.data.traceId}</small>}
        </div>
      </section>
    </div>

    {editor?.type === 'scene' && <SceneEditor
      scene={editor.scene}
      protectedScene={Boolean(editor.scene && isProtected(editor.scene))}
      busy={busy || locked}
      codeTaken={codeTaken}
      onClose={() => setEditor(null)}
      onSave={input => perform(
        () => writeScene(apiBase, input, editor.scene?.id),
        // A new scene opens on its (empty) rules, the next thing to fill in
        result => { if (!editor.scene && result.scene) { setSelected(result.scene.id); setView('scene'); setTab('rules') } },
      )}
    />}
    {editor?.type === 'label' && <LabelEditor label={editor.label} busy={busy || locked} codeTaken={codeTaken} onClose={() => setEditor(null)} onSave={input => perform(() => writeLabel(apiBase, editor.scene.id, input, editor.label?.id))} />}
    {editor?.type === 'rule' && <RuleEditor scene={editor.scene} rule={editor.rule} apiBase={apiBase} busy={busy || locked} onClose={() => setEditor(null)} onSave={input => perform(() => editor.rule
      ? updateRule(apiBase, editor.scene.id, editor.rule.id, input)
      : createRule(apiBase, editor.scene.id, input))} />}
    {importing && <ImportDialog title="上传场景配置" fileName={importing.name} leftoverNoun="场景" prunable run={({ dryRun, prune }) => runImport(importing.document, dryRun, prune)} onApplied={importApplied} onClose={() => setImporting(null)} />}
    {confirmation && <Dialog
      title={confirmation.title}
      size="sm"
      onClose={() => setConfirmation(null)}
      dismissDisabled={busy}
      footer={<div className="scene-dialog-actions">
        <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={busy} onClick={() => setConfirmation(null)}>取消</button>
        <button type="button" className={`mg-btn ${confirmation.danger ? 'scene-danger' : 'mg-primary'}`} disabled={!canWrite} onClick={async () => { if (await confirmation.action()) setConfirmation(null) }}>
          {busy && <LoaderCircle size={15} className="spin" aria-hidden />}{confirmation.confirmLabel}
        </button>
      </div>}
    >
      <p className="scene-confirm">{confirmation.text}</p>
    </Dialog>}
  </div>
}
