import { useState } from 'react'
import { Dialog } from '../../components/Dialog'
import { startSceneSync, stopSceneSync, useSceneSync } from '../scene-sync'
export function SceneSyncPanel({ apiBase, configBusy }: { apiBase: string; configBusy: boolean }) {
  const sync = useSceneSync(); const [limit, setLimit] = useState(200); const [confirm, setConfirm] = useState(false)
  const locked = sync.running || sync.inFlight; const sameBase = sync.base === apiBase
  return <section><h3>全量场景同步</h3><p>扫描全图库、全部场景，不是当前选中场景专用同步。PG 保存不表示 Qdrant 已同步；不重嵌、不创建缺失点、不传 OSS。</p><p>无持久后台 job、全轮快照或跨客户端互斥。其他客户端修改已扫描的数据后，可能需从头显式重跑。</p>
    <label>每批 limit <input type="number" min={1} max={1000} step={1} value={Number.isNaN(limit) ? '' : limit} disabled={locked} onChange={e => setLimit(e.target.value === '' ? NaN : Number(e.target.value))} /></label>
    <button className="mg-btn mg-primary" disabled={locked || configBusy || sync.writes > 0 || !Number.isInteger(limit) || limit < 1 || limit > 1000} onClick={() => setConfirm(true)}>从头全量同步</button>
    {sameBase && ['paused', 'failed'].includes(sync.status) && <button className="mg-btn mg-secondary" disabled={locked || configBusy || sync.writes > 0} onClick={() => startSceneSync(apiBase, sync.limit, false)}>{sync.status === 'failed' ? '按原游标重试本批' : '从上次游标继续'}</button>}
    {locked && <button className="mg-btn mg-secondary" onClick={stopSceneSync}>停止安排后续批</button>}
    {sync.status === 'idle' && <p>未保存持久游标；刷新整站后无法可靠恢复上次位置，只能从头显式重跑。</p>}
    {sameBase && <div role="status"><p>状态：{sync.status} · 当前请求游标：{sync.after ?? '从头'} · limit={sync.limit}</p>
      {sync.batch && <p>最近成功批：处理 {sync.batch.processed}，归属变更 {sync.batch.membershipChanged}，推送 {sync.batch.payloadPushed}。{sync.batch.traceId && `trace_id=${sync.batch.traceId}`}</p>}
      <p>已成功批次合计：{sync.batches} 批 / 处理 {sync.processed} / 变更 {sync.changed} / 推送 {sync.pushed}</p>
      {sync.error && <p className="mg-form-error" role="alert">{sync.error} 本批失败，游标未前移；不会自动重试。</p>}
      {sync.status === 'done' && <p>本轮扫描完成，不表示缺失向量已修复。</p>}
      <p>本会话去重缺失向量 {sync.missing.length} 项；需另走入库或补向量：</p><textarea aria-label="缺失向量 UUID（可复制）" readOnly value={sync.missing.join('\n')} />
    </div>}
    <p>离开页面或点击停止，只停止未发出的后续批；已发请求不会取消，已提交变更保留。返回后不会静默续跑。</p>
    {confirm && <Dialog title="开始全量场景同步" size="sm" onClose={() => setConfirm(false)}><p>将扫描全图库全部场景；进行中本页配置与同窗口人工成员写入禁用。开始？</p><button className="mg-btn mg-primary" disabled={locked || configBusy || sync.writes > 0} onClick={() => { setConfirm(false); startSceneSync(apiBase, limit) }}>确认开始</button></Dialog>}
  </section>
}
