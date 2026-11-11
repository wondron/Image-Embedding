import { useState } from 'react'
import { Cpu, LoaderCircle, Pause, Play, RotateCcw, Square, UploadCloud } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { startSceneSync, stopSceneSync, useSceneSync } from '../scene-sync'
import { useMissingRepair } from './useMissingRepair'

type SyncStatus = ReturnType<typeof useSceneSync>['status']

const STATUS_TEXT: Record<SyncStatus, string> = { idle: '未开始', running: '同步中', paused: '已暂停', failed: '出错了', done: '已完成' }

/**
 * Full sync (POST /api/scenes/sync, in batches): recompute every stored image's scene
 * memberships from the current rules, then write its filter payload (category, scenes, food
 * state, label status) to the vector store. Behaviour lives in features/scene-sync; this is how
 * it reads for the person running it.
 */
export function SceneSyncPanel({ apiBase, configBusy }: { apiBase: string; configBusy: boolean }) {
  const sync = useSceneSync()
  const [limit, setLimit] = useState(200)
  const [confirm, setConfirm] = useState(false)
  const locked = sync.running || sync.inFlight
  const sameBase = sync.base === apiBase
  const status: SyncStatus = sameBase ? sync.status : 'idle'
  const waiting = configBusy || sync.writes > 0  // a configuration or membership write is being saved
  const limitOk = Number.isInteger(limit) && limit >= 1 && limit <= 1000
  const resumable = sameBase && (sync.status === 'paused' || sync.status === 'failed')
  const missing = sameBase ? sync.missing : []
  const { repair, startRepair, stopRepair } = useMissingRepair(apiBase)
  const repairing = Boolean(repair?.running)  // a repair and a sync never run together

  return <section className="scene-sync-panel">
    <div className="scene-sync-intro">
      <h3>全量同步到向量库</h3>
      <p>按当前的场景规则重新计算每张已入库图片的场景归属，再把它的类别、场景、食物状态和标注状态写进向量库，让检索过滤和数据库一致。</p>
      <p className="scene-sync-when">什么时候需要：改了场景规则、移动了类别或改了菜系之后；或者图片数据页出现「待同步」的图片。单张图片的修改在保存时已经自动同步，不用跑这里。</p>
    </div>

    <div className="scene-sync-controls">
      <span className={`scene-sync-state is-${status}`} role="status">{STATUS_TEXT[status]}</span>
      <button type="button" className="mg-btn mg-primary" disabled={locked || waiting || repairing || !limitOk} onClick={() => setConfirm(true)}>
        <UploadCloud size={15} />{status === 'idle' ? '开始同步' : '从头重新同步'}
      </button>
      {resumable && <button type="button" className="mg-btn mg-secondary" disabled={locked || waiting || repairing} onClick={() => startSceneSync(apiBase, sync.limit, false)}>
        {sync.status === 'failed' ? <><RotateCcw size={15} />重试这一批</> : <><Play size={15} />继续</>}
      </button>}
      {locked && <button type="button" className="mg-btn mg-secondary" onClick={stopSceneSync}><Pause size={15} />停止</button>}
    </div>
    {waiting && !locked && <p className="scene-sync-hint">有场景配置或成员修改正在保存，保存完才能开始。</p>}

    {sameBase && status !== 'idle' && <div className="scene-sync-progress">
      <div className="scene-sync-counts" aria-label="同步进度">
        <div><strong>{sync.processed}</strong><span>已检查</span></div>
        <div><strong>{sync.changed}</strong><span>场景归属有变化</span></div>
        <div><strong>{sync.pushed}</strong><span>写入向量库</span></div>
        <div className={missing.length ? 'is-warn' : ''}><strong>{missing.length}</strong><span>没有向量</span></div>
      </div>
      {sync.batch && status !== 'done' && <p className="scene-sync-batch">
        第 {sync.batches} 批：检查 {sync.batch.processed} 张，归属变化 {sync.batch.membershipChanged} 张，写入 {sync.batch.payloadPushed} 张。
      </p>}
      {status === 'running' && <p className="scene-sync-note">正在同步…可以随时停止，停在当前这一批之后。</p>}
      {status === 'paused' && <p className="scene-sync-note">已暂停在第 {sync.batches} 批之后，点「继续」接着往下同步。</p>}
      {status === 'done' && <p className="scene-sync-done">全部图片都检查完了。{missing.length > 0 && `其中 ${missing.length} 张没有向量，同步补不上，可以在下面用原图重新生成。`}</p>}
      {sync.error && <p className="mg-form-error" role="alert">这一批没有完成：{sync.error} 已写入的不受影响，可以「重试这一批」。</p>}
      {missing.length > 0 && <details className="scene-sync-missing" open>
        <summary>没有向量的图片（{missing.length}）</summary>
        <p>这些图片在向量库里没有向量，检索查不到，同步也补不上。可以用存储里的原图重新生成向量，标注不变；原图读不到的，需要重新上传同一张图片。</p>
        <div className="scene-sync-repair">
          {repairing
            ? <>
              <span role="status"><LoaderCircle size={14} className="spin" />正在重新生成 {repair!.done} / {repair!.total}…</span>
              <button type="button" className="mg-btn mg-secondary" onClick={stopRepair}><Square size={14} />停止</button>
            </>
            : <button type="button" className="mg-btn mg-primary" disabled={locked || waiting} onClick={() => void startRepair(missing)}>
              <Cpu size={15} />用原图重新生成向量（{missing.length} 张）
            </button>}
        </div>
        <textarea aria-label="没有向量的图片 ID（可复制）" readOnly value={missing.join('\n')} />
      </details>}
      {repair && !repair.running && <div className="scene-sync-repair-result" role="status">
        <p className={repair.failures.length ? 'is-warn' : 'is-ok'}>
          重新生成向量：已补上 {repair.fixed} 张{repair.gone > 0 && `，${repair.gone} 张已被删除`}{repair.failures.length > 0 && `，${repair.failures.length} 张没成功`}{repair.stopped && `，还有 ${repair.total - repair.done} 张没处理`}。
        </p>
        {repair.failures.length > 0 && <ul>{repair.failures.map(failure => <li key={failure.id}><code>{failure.id}</code>{failure.reason}</li>)}</ul>}
      </div>}
    </div>}

    <details className="scene-sync-more">
      <summary>设置与说明</summary>
      <label className="scene-sync-limit">每批检查
        <input type="number" min={1} max={1000} step={1} value={Number.isNaN(limit) ? '' : limit} disabled={locked} onChange={event => setLimit(event.target.value === '' ? NaN : Number(event.target.value))} />
        张（1–1000）
      </label>
      {!limitOk && <p className="mg-form-error">每批张数需为 1 到 1000 的整数。</p>}
      <ul>
        <li>同步分批进行；进行中，本窗口暂停修改场景配置和图片的场景成员。</li>
        <li>点「停止」或离开页面，会在当前这一批结束后停下；已经写入的不会撤销。</li>
        <li>进度只保存在当前页面，刷新后需要从头再同步一次。</li>
        <li>只写入检索用的过滤信息，不重新计算向量，也补不上缺失的向量。</li>
        <li>其他人同时修改了已经检查过的图片时，可能需要再同步一次。</li>
      </ul>
    </details>

    {confirm && <Dialog title="开始全量同步" size="sm" onClose={() => setConfirm(false)}>
      <div className="scene-sync-confirm">
        <p>将从头检查图库里的全部图片，按当前规则更新场景归属并写入向量库。进行中，本窗口暂停修改场景配置和场景成员。</p>
        <div className="dialog-actions">
          <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus onClick={() => setConfirm(false)}>取消</button>
          <button type="button" className="mg-btn mg-primary" disabled={locked || waiting} onClick={() => { setConfirm(false); startSceneSync(apiBase, limit) }}>开始同步</button>
        </div>
      </div>
    </Dialog>}
  </section>
}
