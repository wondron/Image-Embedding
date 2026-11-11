import { LoaderCircle } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { categoryLabel, type CategoryTreeNode } from '../../entities/category'
import { categoryUses } from './messages'

export interface CategoryDeleteDialogProps {
  /** The node as the tree has it now (its uses may have changed since the dialog opened). */
  entry: CategoryTreeNode
  /** Which request is in flight, if any. */
  submitting: 'delete' | 'deactivate' | null
  notice: string | null
  onClose: () => void
  onDelete: () => void
  onDeactivate: () => void
}

/**
 * Delete a category, or say why it cannot be deleted: the server refuses while a child, an image
 * or a scene rule still uses it. Then the way out is to deactivate it, offered right here.
 */
export function CategoryDeleteDialog({ entry, submitting, notice, onClose, onDelete, onDeactivate }: CategoryDeleteDialogProps) {
  const { node } = entry
  const uses = categoryUses(entry)
  const busy = submitting !== null
  const name = <>「{categoryLabel(node)}」（<code>{node.code}</code>）</>
  return <Dialog title="删除类别" size="sm" dismissDisabled={busy} onClose={onClose}>
    <div className="ct-confirm">
      {uses.length > 0
        ? <>
          <p>{name}还在使用中，不能删除：</p>
          <ul className="ct-uses">{uses.map(use => <li key={use}>{use}</li>)}</ul>
          <p className="ct-confirm-note">{node.isActive
            ? '不再使用时可以改为停用：上传和图库编辑里不再列出它，已有图片和下级类别不受影响。'
            : '它已经停用：上传和图库编辑里不再列出它。'}</p>
        </>
        : <p>确定删除{name}吗？它的全部名称会一起删除，删除后不能恢复。</p>}
      {notice && <p className="mg-form-error" role="alert">{notice}</p>}
      <div className="dialog-actions">
        <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus disabled={busy} onClick={onClose}>{uses.length > 0 ? '关闭' : '取消'}</button>
        {uses.length > 0
          ? node.isActive && <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={onDeactivate}>
            {submitting === 'deactivate' && <LoaderCircle size={14} className="spin" />}
            {submitting === 'deactivate' ? '停用中…' : '改为停用'}
          </button>
          : <button type="button" className="mg-btn ct-danger-solid" disabled={busy} onClick={onDelete}>
            {submitting === 'delete' && <LoaderCircle size={14} className="spin" />}
            {submitting === 'delete' ? '删除中…' : '删除'}
          </button>}
      </div>
    </div>
  </Dialog>
}
