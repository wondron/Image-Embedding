import { useState } from 'react'
import { ImageIcon, ImageOff, LoaderCircle, Trash2 } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { StoredImage } from '../../entities/image'
import type { IndexTask } from '../../entities/task'
import { taskDuration, taskNewImage, taskTime } from './presentation'

/** 成功 / 图片已删除 (succeeded, the image deleted since) / 失败. */
export function taskStateText(task: IndexTask): string {
  if (task.status === 'failed') return '失败'
  return task.imageState === 'deleted' ? '图片已删除' : '成功'
}

/**
 * id: the image's library id, when the API should serve it if its URL cannot be loaded.
 * deleted: the image is gone from the library (its record is kept): a marker instead of a picture.
 */
export function TaskThumbnail({ url, id = null, deleted = false }: { url: string | null; id?: string | null; deleted?: boolean }) {
  if (deleted) {
    return <span className="mg-task-thumb index-task-placeholder is-deleted" role="img" aria-label="图片已删除"><ImageOff size={18} aria-hidden /></span>
  }
  return <StoredImage
    id={id}
    url={url}
    className="mg-task-thumb"
    placeholder={<span className="mg-task-thumb index-task-placeholder" aria-label="无预览"><ImageIcon size={18} aria-hidden /></span>}
  />
}

/** One record, read-only, with 「删除这条记录」 (confirmed; `onDelete` runs it and closes the dialog when done). */
export function TaskDetails({ task, readTraceId, onClose, onDelete }: {
  task: IndexTask
  readTraceId: string | null
  onClose: () => void
  onDelete: () => Promise<void>
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  async function remove() {
    setBusy(true)
    try { await onDelete() } finally { setBusy(false) }
  }
  const fields = [
    ['记录 ID', task.id], ['文件名', task.filename], ['状态', taskStateText(task)],
    ['是否新图', taskNewImage(task.newImage)], ['图片 ID', task.imageId ?? '—'],
    ['服务端单文件耗时', taskDuration(task.durationMs)], ['记录插入时间', taskTime(task.createdAt)],
    ['错误码', task.errorCode ?? '—'], ['失败公开说明', task.errorDetail ?? '—'],
    ['原入库请求 trace', task.traceId ?? '—'], ['列表数据读取 trace（成功 GET）', readTraceId ?? '—'],
  ]
  const footer = <div className="index-tasks-dialog-actions">
    {confirming
      ? <>
        <span className="index-tasks-confirm-inline">只删这条记录，不影响图片；删除后不能恢复。</span>
        <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={() => setConfirming(false)}>取消</button>
        <button type="button" className="mg-btn index-tasks-danger" disabled={busy} onClick={() => void remove()}>
          {busy ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}确认删除
        </button>
      </>
      : <>
        <button type="button" className="mg-btn index-tasks-danger-quiet" onClick={() => setConfirming(true)}><Trash2 size={15} />删除这条记录</button>
        <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus onClick={onClose}>关闭</button>
      </>}
  </div>
  return <Dialog title="入库记录详情" size="sm" onClose={onClose} dismissDisabled={busy} footer={footer}>
    <div className="index-task-details">
      <dl>{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <h3>当前图片（不是历史快照）</h3>
      {task.image ? <><TaskThumbnail url={task.image.imageUrl} /><dl>
        <div><dt>当前图片 ID</dt><dd>{task.image.id}</dd></div>
        <div><dt>类别 ID</dt><dd>{task.image.categoryId ?? '—'}</dd></div>
        <div><dt>类别</dt><dd>{task.image.category ?? '—'}</dd></div>
        <div><dt>标注状态</dt><dd>{task.image.labelStatus ?? '—'}</dd></div>
      </dl></>
        : task.status === 'failed' ? <p>入库失败，没有得到图片。</p>
        : task.imageState === 'deleted'
          ? <p className="index-task-deleted-note"><ImageOff size={15} aria-hidden />这张图片已从图库删除。记录保留下来，用来追溯这个文件什么时候入过库；不再需要可以删除这条记录，或在列表上方「清理图片已删除的记录」。</p>
          : <p>当前图片信息不可用。</p>}
      <p>记录仅表示该次同步入库结果，不含后续人工场景调整；失败也不保证零副作用。记录可能缺行，不承诺永久保留。</p>
    </div>
  </Dialog>
}
