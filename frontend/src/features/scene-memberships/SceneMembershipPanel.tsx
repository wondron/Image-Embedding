import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError } from '../../shared/api'
import { readMemberships, sceneError, uncertainWrite, writeMembership, type Membership, type MembershipSnapshot, type VectorSync } from '../../entities/scene'
import { useSceneCatalog, markScenePending } from '../scene-catalog'
import { beginSceneWrite, endSceneWrite, sceneWritesLocked, useSceneSync } from '../scene-sync'

// Writes of one image are serialized across every open panel of this window.
const busyImages = new Set<string>(); const listeners = new Set<() => void>()
function lock(image: string, locked: boolean) { if (locked) busyImages.add(image); else busyImages.delete(image); listeners.forEach(fn => fn()) }
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } }

/** What the server did with the image's search payload after saving (vector_sync). */
const SAVED: Record<VectorSync, string> = {
  pushed: '已保存，检索已生效。',
  missing: '已保存到数据库。这张图在当前向量库里没有向量，检索查不到它；可以用原图重新生成向量（见上方向量库）。',
  not_ready: '已保存到数据库；检索服务未就绪，检索要等同步后才能看到这次修改。',
  failed: '已保存到数据库；更新检索索引失败，可稍后再保存一次或到场景管理执行同步。',
}

function source(m: Membership): { text: string; manual: boolean } {
  if (m.origin === 'implicit') return { text: '全包含', manual: false }
  if (m.manualOverride === true) return { text: '人工纳入', manual: true }
  if (m.manualOverride === false) return { text: '人工排除', manual: true }
  if (m.origin === 'rule') return { text: '规则', manual: false }
  return { text: '—', manual: false }
}

/**
 * Scene membership of one image: which scenes it is in, why, and manual include / exclude /
 * back-to-rules. Each click is one write; the server pushes the new payload to the search index
 * when it can, so most changes need no sync.
 */
export function SceneMembershipPanel({ imageId, apiBase = '', onSaved, onManage }: { imageId: string; apiBase?: string; onSaved?: () => void; onManage?: () => void }) {
  const catalog = useSceneCatalog(apiBase); const sync = useSceneSync()
  const key = `${apiBase}:${imageId}`
  const busy = useSyncExternalStore(subscribe, () => busyImages.has(key))
  const [snapshot, setSnapshot] = useState<MembershipSnapshot | null>(null)
  const [unsupported, setUnsupported] = useState(false); const [ready, setReady] = useState(false); const [loading, setLoading] = useState(false)
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [needsSync, setNeedsSync] = useState(false)
  const generation = useRef(0)
  const read = useCallback(async () => {
    const g = ++generation.current; setLoading(true); setReady(false)
    try { const value = await readMemberships(apiBase, imageId); if (g === generation.current) { setSnapshot(value); setReady(true) } }
    catch (e) { if (g === generation.current) setError(`读取场景成员失败，暂不可修改。${sceneError(e)}`) }
    finally { if (g === generation.current) setLoading(false) }
  }, [apiBase, imageId])
  useEffect(() => { setSnapshot(null); setUnsupported(false); setError(''); setNotice(''); setNeedsSync(false); void read(); return () => { generation.current++ } }, [read])

  async function write(sceneId: number, isMember: boolean | null) {
    if (!ready || unsupported || busyImages.has(key) || sceneWritesLocked()) return
    if (!beginSceneWrite()) return
    lock(key, true); setError(''); setNotice('')
    const g = generation.current
    try {
      const value = await writeMembership(apiBase, imageId, sceneId, isMember)
      if (value.syncRequired) markScenePending(apiBase)
      if (g === generation.current) {
        setSnapshot(value); setNeedsSync(value.syncRequired)
        setNotice(value.vectorSync ? SAVED[value.vectorSync] : '已保存到数据库，检索要等同步后才能看到这次修改。')
        onSaved?.()
      }
    } catch (e) {
      if (g === generation.current) {
        setError(sceneError(e))
        if (e instanceof ApiError && (e.status === 405 || e.status === 501 || (e.status === 404 && e.code !== 'SCENE_NOT_FOUND'))) setUnsupported(true)
        if (uncertainWrite(e)) { setReady(false); setNotice('保存结果不确定，已重新读取成员；不会自动重试，请核对后再操作。'); await read() }
      }
    } finally { lock(key, false); endSceneWrite() }
  }

  const syncing = sync.running || sync.inFlight
  const disabled = !ready || unsupported || busy || loading || syncing
  const nameById = new Map(catalog.data?.scenes.map(s => [s.id, s.name]))
  const nameByCode = new Map(catalog.data?.scenes.map(s => [s.code, s.name]))
  const configPending = catalog.pendingSync && !notice
  return <section className="scene-membership-panel" aria-busy={busy || loading}>
    <div className="scene-membership-head"><h4>场景成员</h4><button type="button" className="text-button" disabled={busy || loading} onClick={() => { setError(''); void read() }}>刷新</button></div>
    {loading && <p role="status">正在读取场景成员…</p>}
    {error && <p role="alert" className="mg-form-error">{error}</p>}
    {unsupported && <p role="alert">人工成员写接口不可用，本面板只读。</p>}
    {syncing && <p role="status">全量同步进行中，暂不可修改成员。</p>}
    {notice && <p role="status">{notice}{needsSync && onManage && <> <button type="button" className="text-button" onClick={onManage}>去同步</button></>}</p>}
    {configPending && <p role="status">场景配置有尚未同步的改动，这里显示的是同步前的归属。{onManage && <> <button type="button" className="text-button" onClick={onManage}>去同步</button></>}</p>}
    {snapshot && <>
      <p>当前所属：{snapshot.scenes.map(code => nameByCode.get(code) ?? code).join('、') || '无'}</p>
      <div className="scene-table-scroll"><table className="scene-table scene-membership-table"><thead><tr><th>场景</th><th>成员</th><th>来源</th><th>人工调整</th></tr></thead><tbody>
        {snapshot.memberships.map(m => {
          const from = source(m)
          return <tr key={m.sceneId}>
            <th>{nameById.get(m.sceneId) ?? m.sceneCode}{!m.isActive && <span className="scene-inactive-tag">停用</span>}<small>{m.sceneCode}</small></th>
            <td>{m.isMember ? '是' : '否'}</td>
            <td className={`scene-membership-source${from.manual ? ' is-manual' : ''}`}>{from.text}</td>
            <td>{m.includeAll ? <small>全包含场景不可调整</small> : <div className="scene-membership-actions">
              {m.isActive && <>
                <button type="button" className="text-button" disabled={disabled || m.manualOverride === true} onClick={() => void write(m.sceneId, true)}>纳入</button>
                <button type="button" className="text-button" disabled={disabled || m.manualOverride === false} onClick={() => void write(m.sceneId, false)}>排除</button>
              </>}
              {m.manualOverride !== null && <button type="button" className="text-button" title="清除人工调整，按当前规则重新判断（不等于排除）" disabled={disabled} onClick={() => void write(m.sceneId, null)}>恢复规则</button>}
              {!m.isActive && m.manualOverride === null && <small>场景已停用</small>}
            </div>}</td>
          </tr>
        })}
      </tbody></table></div>
      <p className="mg-help">人工调整优先于规则；「恢复规则」只清除人工调整，按当前规则重新判断，不等于排除。</p>
      <small title={snapshot.traceId ? `trace_id=${snapshot.traceId}` : undefined}>数据版本 v{snapshot.payloadVersion}（场景、类别、食物状态或标注状态变化时加 1）</small>
    </>}
  </section>
}
