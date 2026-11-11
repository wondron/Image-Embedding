import { useState } from 'react'
import { ImageIcon } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import type { IndexTask } from '../../entities/task'
import { taskDuration, taskNewImage, taskTime } from './presentation'

export function TaskThumbnail({ url }: { url: string | null }) {
  const [failed, setFailed] = useState<string | null>(null)
  return url && failed !== url
    ? <img className="mg-task-thumb" src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(url)} />
    : <span className="mg-task-thumb index-task-placeholder" aria-label="无预览"><ImageIcon size={18} aria-hidden /></span>
}

export function TaskDetails({ task, readTraceId, onClose }: { task: IndexTask; readTraceId: string | null; onClose: () => void }) {
  const fields = [
    ['记录 ID', task.id], ['文件名', task.filename], ['历史终态', task.status === 'succeeded' ? '成功' : '失败'],
    ['是否新图', taskNewImage(task.newImage)], ['图片 ID', task.imageId ?? '—'],
    ['服务端单文件耗时', taskDuration(task.durationMs)], ['记录插入时间', taskTime(task.createdAt)],
    ['错误码', task.errorCode ?? '—'], ['失败公开说明', task.errorDetail ?? '—'],
    ['原入库请求 trace', task.traceId ?? '—'], ['列表数据读取 trace（成功 GET）', readTraceId ?? '—'],
  ]
  return <Dialog title="入库记录详情" size="sm" onClose={onClose}>
    <div className="index-task-details">
      <dl>{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <h3>当前图片投影（不是历史快照）</h3>
      {task.image ? <><TaskThumbnail url={task.image.imageUrl} /><dl>
        <div><dt>当前图片 ID</dt><dd>{task.image.id}</dd></div>
        <div><dt>类别 ID</dt><dd>{task.image.categoryId ?? '—'}</dd></div>
        <div><dt>类别</dt><dd>{task.image.category ?? '—'}</dd></div>
        <div><dt>标注状态</dt><dd>{task.image.labelStatus ?? '—'}</dd></div>
      </dl></> : <p>当前图片信息不可用（可能已删除）</p>}
      <p>记录仅表示该次同步入库结果，不含后续人工场景调整；失败也不保证零副作用。记录可能缺行，不承诺永久保留。</p>
      <button type="button" className="mg-btn mg-secondary" onClick={onClose}>关闭</button>
    </div>
  </Dialog>
}
