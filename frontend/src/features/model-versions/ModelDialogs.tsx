import { useState } from 'react'
import { ArrowRightLeft, LoaderCircle, PackagePlus, Trash2, TriangleAlert } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { MODEL_LABEL_PATTERN, type EmbeddingModel } from '../../entities/embedding-model'
import { defaultVersion, modelFileStem } from './messages'

function useBusy() {
  const [busy, setBusy] = useState(false)
  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } finally { setBusy(false) }
  }
  return [busy, run] as const
}

/** Register the configured encoder as a new version; the parent closes the dialog once it is done. */
export function RegisterModelDialog({ modelPath, dim, onConfirm, onClose }: {
  modelPath: string | null
  dim: number | null
  onConfirm: (name: string, version: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(() => modelFileStem(modelPath))
  const [version, setVersion] = useState(() => defaultVersion(new Date()))
  const [busy, run] = useBusy()
  const valid = MODEL_LABEL_PATTERN.test(name.trim()) && MODEL_LABEL_PATTERN.test(version.trim())
  const footer = <div className="model-dialog-actions">
    <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onClose}>取消</button>
    <button type="button" className="mg-btn mg-primary" disabled={busy || !valid} onClick={() => void run(() => onConfirm(name.trim(), version.trim()))}>
      {busy ? <LoaderCircle size={15} className="spin" /> : <PackagePlus size={15} />}登记
    </button>
  </div>
  return <Dialog title="登记新的模型版本" size="sm" onClose={onClose} dismissDisabled={busy} footer={footer}>
    <div className="model-dialog">
      <p>把 EMBEDDING_CONFIG 现在配置的编码器登记为一个新版本，并为它新建一个空的向量库。检索和入库仍用当前版本，什么都不会变。</p>
      <dl className="model-dialog-facts">
        <div><dt>权重</dt><dd><code>{modelPath ?? '—'}</code></dd></div>
        <div><dt>向量维度</dt><dd>{dim ?? '—'}</dd></div>
      </dl>
      <label className="model-dialog-field">名称
        <input data-dialog-initial-focus value={name} maxLength={64} disabled={busy} onChange={event => setName(event.target.value)} />
      </label>
      <label className="model-dialog-field">版本
        <input value={version} maxLength={64} disabled={busy} onChange={event => setVersion(event.target.value)} />
      </label>
      {!valid && <p className="mg-form-error">名称和版本只能用字母、数字、点、下划线和连字符，以字母或数字开头，最多 64 个字符。</p>}
      <p className="model-dialog-note">向量库名由名称和版本生成；名称加版本不能和已有版本重复。</p>
    </div>
  </Dialog>
}

/** Switch search and index to a version (or back to an earlier one). */
export function ActivateModelDialog({ model, serving, images, onConfirm, onClose }: {
  model: EmbeddingModel
  serving: EmbeddingModel | null
  images: number
  onConfirm: (allowMissing: boolean) => Promise<void>
  onClose: () => void
}) {
  const [accept, setAccept] = useState(false)
  const [busy, run] = useBusy()
  const incomplete = model.missing > 0
  const reenable = model.status === 'current'
  const footer = <div className="model-dialog-actions">
    <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={busy} onClick={onClose}>取消</button>
    <button type="button" className="mg-btn mg-primary" disabled={busy || (incomplete && !accept)} onClick={() => void run(() => onConfirm(incomplete))}>
      {busy ? <LoaderCircle size={15} className="spin" /> : <ArrowRightLeft size={15} />}{reenable ? '重新启用' : `切换到 ${model.label}`}
    </button>
  </div>
  return <Dialog title={reenable ? '重新启用检索' : model.activatedAt ? '切回这个版本' : '切换模型版本'} size="sm" onClose={onClose} dismissDisabled={busy} footer={footer}>
    <div className="model-dialog">
      <p>
        检索和入库将立即改用 <strong>{model.label}</strong>（向量库 <code>{model.collection}</code>）。
        {serving && serving.id !== model.id && <>原来的 {serving.label} 保留为「可切换」，向量不删，随时可以切回。</>}
      </p>
      {model.encoder === 'recorded' && <p className="model-dialog-note">它的编码器现在没有加载，会按记录的权重 <code>{model.weightsUri}</code> 重新加载，需要几秒；权重文件不在了就切换不了。</p>}
      {incomplete && <div className="model-dialog-warn" role="alert">
        <TriangleAlert size={16} aria-hidden />
        <div>
          <p>还有 <strong>{model.missing}</strong> / {images} 张图片没有这个版本的向量，切换后检索查不到它们，直到补齐。</p>
          <label><input type="checkbox" checked={accept} disabled={busy} onChange={event => setAccept(event.target.checked)} />我知道，仍然切换</label>
        </div>
      </div>}
      <p className="model-dialog-note">只切换本服务进程；多进程部署时其他进程要重启。</p>
    </div>
  </Dialog>
}

/** Drop a version that is not current: its collection and ledger rows go. */
export function RetireModelDialog({ model, onConfirm, onClose }: {
  model: EmbeddingModel
  onConfirm: () => Promise<void>
  onClose: () => void
}) {
  const [busy, run] = useBusy()
  const footer = <div className="model-dialog-actions">
    <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={busy} onClick={onClose}>取消</button>
    <button type="button" className="mg-btn model-danger" disabled={busy} onClick={() => void run(onConfirm)}>
      {busy ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}下线并删除向量
    </button>
  </div>
  return <Dialog title="下线模型版本" size="sm" onClose={onClose} dismissDisabled={busy} footer={footer}>
    <div className="model-dialog">
      <p>
        将删除 <strong>{model.label}</strong> 的向量库 <code>{model.collection}</code>（{model.embedded} 张图片的向量）和它的写入记录，
        状态改为「已下线」。之后不能再切换到它；要再用，得重新登记并生成向量。
      </p>
      <p className="model-dialog-note">图库里的图片和标注不受影响，当前版本的检索也不受影响。</p>
    </div>
  </Dialog>
}
