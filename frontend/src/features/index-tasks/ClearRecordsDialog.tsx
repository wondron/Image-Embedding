import { useState, type ReactNode } from 'react'
import { LoaderCircle, Trash2 } from 'lucide-react'
import { Dialog } from '../../components/Dialog'

/** Which records a clear removes: those of deleted images, or the failures. */
export type ClearScope = 'deleted' | 'failed'

const COPY: Record<ClearScope, { title: string; text: (count: number) => ReactNode }> = {
  deleted: {
    title: '清理图片已删除的记录',
    text: count => <>将删除 <strong>{count}</strong> 条入库记录，它们对应的图片已经从图库删除。只删记录，图库里的图片不受影响；删除后不能恢复。</>,
  },
  failed: {
    title: '清空失败记录',
    text: count => <>将删除 <strong>{count}</strong> 条失败记录。只删记录；还要重新上传的文件，请先记下文件名。删除后不能恢复。</>,
  },
}

/** Confirm a clear; `onConfirm` runs it (the parent closes the dialog when it is done). */
export function ClearRecordsDialog({ scope, count, onConfirm, onClose }: {
  scope: ClearScope
  count: number
  onConfirm: () => Promise<void>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  async function confirm() {
    setBusy(true)
    try { await onConfirm() } finally { setBusy(false) }
  }
  const footer = <div className="index-tasks-dialog-actions">
    <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={busy} onClick={onClose}>取消</button>
    <button type="button" className="mg-btn index-tasks-danger" disabled={busy} onClick={() => void confirm()}>
      {busy ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}确认清理 {count} 条
    </button>
  </div>
  return <Dialog title={COPY[scope].title} size="sm" onClose={onClose} dismissDisabled={busy} footer={footer}>
    <p className="index-tasks-confirm">{COPY[scope].text(count)}</p>
  </Dialog>
}
