import { ArrowRight, Cpu, LoaderCircle, RefreshCw, UploadCloud } from 'lucide-react'
import type { ImageLedgerEntry, ImageVectorState } from '../../entities/image'
import { formatLibraryTime } from '../../shared/lib'
import { VECTOR_STATUS_TEXT, payloadDiffRows, unavailableReason, type PayloadNames } from './vectorView'

const MODEL_STATUS_TEXT: Readonly<Record<string, string>> = { current: '当前', ready: '待启用', building: '构建中', retired: '已退役' }

export interface VectorCardProps {
  vector: ImageVectorState
  /** image_embeddings rows of the image: which model versions' collections have its point. */
  ledger: readonly ImageLedgerEntry[]
  names: PayloadNames
  /** Another request of the dialog runs: the actions wait. */
  busy: boolean
  pushing: boolean
  reembedding: boolean
  onPush: () => void
  /** Embed the stored original again (repairs a missing vector). */
  onReembed: () => void
  onReload: () => void
  /** Go to 上传与导入 (uploading the same image again restores a missing vector too). */
  onUpload?: () => void
}

/**
 * The image in the current model's vector collection: whether search filters see what the
 * database says, what lags behind (in words, not ids and codes), and the way to fix it. The
 * collection, versions and ledger rows stay under 「技术信息」.
 */
export function VectorCard({ vector, ledger, names, busy, pushing, reembedding, onPush, onReembed, onReload, onUpload }: VectorCardProps) {
  const view = VECTOR_STATUS_TEXT[vector.status]
  const rows = vector.status === 'stale' ? payloadDiffRows(vector, names) : []
  const current = ledger.find(entry => entry.collection === vector.collection)
  const others = ledger.filter(entry => entry !== current)
  // The ledger says the point was written, the collection says it is not there
  const ledgerDisagrees = vector.status === 'missing' && vector.syncedVersion !== null
  const behind = vector.payloadVersion !== null && vector.syncedVersion !== null && vector.syncedVersion < vector.payloadVersion

  return <section className={`ie-vector is-${vector.status}`} aria-label="向量库状态">
    <div className="ie-vector-head">
      <strong>向量库</strong>
      <span className="ie-vector-badge">{view.label}</span>
      {vector.status === 'unavailable' && <button type="button" className="mg-text-button" disabled={busy} onClick={onReload}><RefreshCw size={13} />刷新</button>}
    </div>
    <p>{vector.status === 'unavailable' ? unavailableReason(vector.detail) : view.help}</p>

    {rows.length > 0 && <table className="ie-diff">
      <thead><tr><th scope="col">不一致</th><th scope="col">向量库里</th><th scope="col">数据库里</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.key}><th scope="row">{row.label}</th><td>{row.stored}</td><td>{row.expected}</td></tr>)}</tbody>
    </table>}

    {vector.status === 'stale' && <button type="button" className="mg-btn mg-primary ie-vector-action" disabled={busy} onClick={onPush}>
      {pushing ? <LoaderCircle size={14} className="spin" /> : <UploadCloud size={14} />}{pushing ? '正在同步…' : '按数据库同步到向量库'}
    </button>}
    {vector.status === 'missing' && <div className="ie-vector-actions">
      <button type="button" className="mg-btn mg-primary ie-vector-action" disabled={busy} onClick={onReembed}>
        {reembedding ? <LoaderCircle size={14} className="spin" /> : <Cpu size={14} />}{reembedding ? '正在生成向量…' : '用存储里的原图重新生成向量'}
      </button>
      {onUpload && <button type="button" className="mg-text-button ie-vector-link" disabled={busy} onClick={onUpload}>
        或去「上传与导入」重新上传<ArrowRight size={13} />
      </button>}
    </div>}

    <details className="ie-tech">
      <summary>技术信息</summary>
      <dl>
        {vector.collection && <div><dt>向量库</dt><dd><code>{vector.collection}</code>{vector.embeddingSpaceId && <>（模型 <code>{vector.embeddingSpaceId}</code>）</>}</dd></div>}
        <div><dt>数据版本</dt><dd>
          数据库 v{vector.payloadVersion ?? '—'} · 写入记录 v{vector.syncedVersion ?? '—'}
          {behind && vector.status !== 'missing' && <span className="ie-tech-note">（记录落后于数据库）</span>}
        </dd></div>
        {vector.embeddedAt && <div><dt>写入向量</dt><dd>{formatLibraryTime(vector.embeddedAt)}</dd></div>}
        {ledgerDisagrees && <div><dt>注意</dt><dd>写入记录显示已写到 v{vector.syncedVersion}，但向量库里找不到这个点，可能被删除或 collection 被重建过。</dd></div>}
        {others.length > 0 && <div><dt>其他模型</dt><dd>{others.map(entry => <span className="ie-tech-ledger" key={entry.modelId}>
          <code>{entry.name}:{entry.version}</code>（{MODEL_STATUS_TEXT[entry.modelStatus] ?? entry.modelStatus}）写入 v{entry.syncedVersion ?? '—'}
        </span>)}</dd></div>}
        {vector.status === 'unavailable' && vector.detail && <div><dt>原因</dt><dd className="ie-tech-raw">{vector.detail}</dd></div>}
      </dl>
    </details>
  </section>
}
