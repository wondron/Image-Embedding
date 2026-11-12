import { useState } from 'react'
import { CircleCheck, CloudOff, LoaderCircle, RefreshCw, Trash2 } from 'lucide-react'
import type { ImageDeletionFailure } from '../../entities/task'
import { EmptyState, Metric, notify } from '../../shared/ui'
import { TaskThumbnail, taskTime } from '../index-tasks'
import { cleanupNotice } from './cleanupRun'
import { QUEUE_SHOWN, useDeletionQueue } from './useDeletionQueue'
import './deletion-queue.css'

/** Why a cleanup request itself was refused, in words. */
function refusalText(code: string | null, message: string, traceId: string | null): string {
  const reason = code === 'NOT_READY'
    ? '检索服务未就绪（向量库或对象存储没有启动），现在不能清理；恢复后后台会自动重试。'
    : message
  return traceId ? `${reason}（追踪号：${traceId}）` : reason
}

/**
 * 待清理图片: images already deleted from the database whose vector point and stored files are
 * still to be removed. A delete cleans them at once; what failed then waits here for the
 * background reconciler or 「立即清理」.
 */
export function DeletionQueuePanel({ apiBase }: { apiBase: string }) {
  const queue = useDeletionQueue(apiBase)
  const [failures, setFailures] = useState<ImageDeletionFailure[]>([])
  const { data, loading, error, cleaning } = queue
  const ready = data?.cleanupReady === true

  async function clean() {
    const result = await queue.clean()
    if (!result) return
    const { totals, error: refused } = result
    setFailures(totals.failed)
    if (refused && totals.runs === 0) {
      notify.error(refusalText(refused.code, refused.message, refused.traceId), { title: '没有清理' })
      return
    }
    if (refused) {
      notify.warn(`已清理 ${totals.processed} 张，之后的清理请求失败：${refusalText(refused.code, refused.message, refused.traceId)}`, { title: '清理中断' })
      return
    }
    const notice = cleanupNotice(totals)
    if (notice.type === 'warn') notify.warn(notice.text, { title: notice.title })
    else notify[notice.type](notice.text)
  }

  const failureOf = new Map(failures.map(item => [item.imageId, item]))
  return <section className="deletion-queue" aria-label="待清理图片" aria-busy={loading || cleaning}>
    <div className="mg-metrics">
      <Metric label="待清理" value={data ? String(data.total) : '—'} detail="已从数据库删除，向量和文件还没删掉" icon={Trash2} />
      <Metric
        label="能否清理"
        value={data ? (ready ? '可以清理' : '暂时不能') : '—'}
        detail={data ? (ready ? '检索服务就绪，可以立即清理' : '检索服务未就绪（向量库或对象存储），恢复后再清理') : '读取中'}
        icon={data && !ready ? CloudOff : CircleCheck}
      />
    </div>
    <div className="mg-page-toolbar">
      <button type="button" className="mg-btn mg-secondary" disabled={loading || cleaning} onClick={() => void queue.refresh()}><RefreshCw size={15} />刷新</button>
      <button type="button" className="mg-btn mg-primary" disabled={!data || data.total === 0 || !ready || loading || cleaning} onClick={() => void clean()}>
        {cleaning ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}{cleaning ? '正在清理…' : '立即清理'}
      </button>
      <span className="deletion-queue-caption">上次读取：{taskTime(queue.readAt)}</span>
    </div>
    <div className="deletion-queue-status" aria-live="polite">
      {loading && <p role="status"><LoaderCircle size={15} className="spin" aria-hidden />{data ? '刷新中（显示上次结果）' : '正在读取待清理图片…'}</p>}
      {error && <div role="alert" className="mg-form-error">
        <p>{data ? '刷新失败，显示上次读取的结果。' : '读取待清理图片失败。'} {error.message}</p>
        <p>错误码：{error.code ?? '—'} · 追踪号：{error.traceId ?? '—'}</p>
        <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={() => void queue.refresh()}>重试读取</button>
      </div>}
    </div>
    {data && <section className="mg-panel" aria-label="待清理的图片">
      {data.items.length > 0 ? <>
        <div role="list">{data.items.map(item => {
          const failure = failureOf.get(item.imageId)
          return <article className={`deletion-queue-row${failure ? ' is-failed' : ''}`} role="listitem" key={item.imageId}>
            <TaskThumbnail url={item.imageUrl} id={item.imageId} />
            <div className="deletion-queue-id">
              <code title={item.imageId}>{item.imageId}</code>
              {failure && <small>清理失败：{failure.detail}（{failure.code}）</small>}
            </div>
            <span className="deletion-queue-time"><small>删除于 </small>{taskTime(item.deletedAt)}</span>
          </article>
        })}</div>
        {data.total > data.items.length && <p className="deletion-queue-more">只列出最早删除的 {QUEUE_SHOWN} 张，共 {data.total} 张；清理会从最早的开始。</p>}
      </> : <EmptyState icon={CircleCheck} title="没有待清理的图片" description="删除的图片都已清理完：向量库里的点和存储里的原图、缩略图都删掉了。" />}
    </section>}
    <p className="deletion-queue-caption">删除图片时，数据库里的记录当场删除，图片随即从图库和检索结果里消失；向量库里的点和存储里的原图、缩略图紧接着清理。当时没清理掉的（例如向量库或对象存储暂时不可用）留在这里，后台会定时重试，也可以点「立即清理」。</p>
  </section>
}
