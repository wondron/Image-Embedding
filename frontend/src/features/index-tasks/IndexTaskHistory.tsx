import { useEffect, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, ImageOff, ListChecks, LoaderCircle, RefreshCw, Trash2, XCircle } from 'lucide-react'
import { EmptyState, Metric, notify } from '../../shared/ui'
import { clearIndexTasks, deleteIndexTask, type IndexTask, type IndexTaskCounts } from '../../entities/task'
import { ClearRecordsDialog, type ClearScope } from './ClearRecordsDialog'
import { TaskDetails, TaskThumbnail, taskStateText } from './TaskDetails'
import { taskDuration, taskNewImage, taskTime } from './presentation'
import { TASK_PAGE_SIZE, useIndexTaskHistory, type TaskView } from './useIndexTaskHistory'
import './index-tasks.css'

const views: { view: TaskView; label: string }[] = [
  { view: 'all', label: '全部' }, { view: 'succeeded', label: '成功' },
  { view: 'deleted', label: '图片已删除' }, { view: 'failed', label: '失败' },
]

/** How many records a view holds, from the counts (which ignore the current view). */
function viewCount(view: TaskView, counts: IndexTaskCounts): number {
  if (view === 'all') return counts.succeeded + counts.failed
  if (view === 'succeeded') return counts.succeeded - counts.imageDeleted
  return view === 'deleted' ? counts.imageDeleted : counts.failed
}

function failure(cause: unknown): string {
  return cause instanceof Error ? cause.message : '请稍后重试。'
}

function statusClass(task: IndexTask): string {
  if (task.status === 'failed') return 'mg-job-status-error'
  return task.imageState === 'deleted' ? 'index-tasks-status-deleted' : 'mg-job-status-success'
}

/**
 * 服务端入库记录. Deleting an image keeps its records (they tell what was indexed when): they read
 * 「图片已删除」, have their own view, and are cleared on demand, like the failures, or one by one.
 */
export function IndexTaskHistory({ apiBase }: { apiBase: string }) {
  const history = useIndexTaskHistory(apiBase)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [clearing, setClearing] = useState<ClearScope | null>(null)
  const { data, loading, error, query } = history
  const selected = data?.items.find(item => item.id === selectedId)
  const counts = data?.counts
  useEffect(() => { setSelectedId(null) }, [history.key])
  useEffect(() => {
    if (selectedId !== null && data && !loading && !error && !data.items.some(item => item.id === selectedId)) {
      setSelectedId(null); notify.info('该记录不在当前页，请重新选择。')
    }
  }, [selectedId, data, loading, error])

  async function clear(scope: ClearScope) {
    try {
      const deleted = await clearIndexTasks(scope === 'deleted' ? { imageState: 'deleted' } : { status: 'failed' }, { baseUrl: apiBase })
      notify.success(deleted > 0 ? `已清理 ${deleted} 条入库记录。` : '没有可清理的记录，可能已经清理过了。')
      setClearing(null); setSelectedId(null)
      history.reload()
    } catch (cause) {
      notify.error(failure(cause), { title: '清理失败' })
    }
  }
  async function removeRecord(task: IndexTask) {
    try {
      const deleted = await deleteIndexTask(task.id, { baseUrl: apiBase })
      notify.success(deleted ? '已删除这条入库记录。' : '这条记录已经不在了。')
      setSelectedId(null)
      history.reload()
    } catch (cause) {
      notify.error(failure(cause), { title: '删除失败' })
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / TASK_PAGE_SIZE)) : 1
  const allEmpty = counts?.succeeded === 0 && counts?.failed === 0
  const errorHint = error?.status === 404 ? '请核对运行版本或代理路径；此响应不表示源码没有接口。'
    : error?.code === 'NOT_READY' ? '任务目录服务尚未就绪，不代表暂无记录。'
    : error?.status === 422 ? '查询参数不合法，可回到默认筛选再试。' : ''
  const emptyCopy = allEmpty
    ? { title: '暂无服务端入库记录', description: '这里只含接入后实际记录的逐文件结果；写记录可能缺行，不表示图库没有图片，也不承诺永久保留。' }
    : query.view === 'deleted'
      ? { title: '没有图片已删除的记录', description: '从图库删除图片后，它的入库记录会留在这里，标为「图片已删除」，可以清理。' }
      : { title: '当前筛选无记录', description: '全历史仍有记录，当前结果筛选没有匹配项。' }
  return <section className="index-tasks" aria-label="服务端入库记录">
    <div className="mg-metrics">
      <Metric label="成功（图片在库）" value={counts ? String(counts.succeeded - counts.imageDeleted) : '—'} detail="成功入库，图片仍在图库" icon={Check} />
      <Metric label="图片已删除" value={counts ? String(counts.imageDeleted) : '—'} detail="入库成功过，图片之后被删除；记录保留，可清理" icon={ImageOff} />
      <Metric label="失败记录" value={counts ? String(counts.failed) : '—'} detail="可重新上传；处理完可以清空" icon={XCircle} />
    </div>
    <div className="mg-page-toolbar">
      <div className="mg-tabs" role="group" aria-label="历史结果筛选">{views.map(({ view, label }) => <button type="button" key={view} className={query.view === view ? 'active' : ''} aria-pressed={query.view === view} onClick={() => history.filter(view)}>{label}{counts ? `（${viewCount(view, counts)}）` : ''}</button>)}</div>
      <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={history.refresh}><RefreshCw size={15} />刷新</button>
      <button type="button" className="mg-btn index-tasks-danger-quiet" disabled={loading || !counts?.imageDeleted} onClick={() => setClearing('deleted')}><Trash2 size={15} />清理图片已删除的记录</button>
      <button type="button" className="mg-btn index-tasks-danger-quiet" disabled={loading || !counts?.failed} onClick={() => setClearing('failed')}><Trash2 size={15} />清空失败记录</button>
      <span className="index-tasks-caption">上次成功读取：{taskTime(history.readAt)}</span>
    </div>
    <div className="index-tasks-status" aria-live="polite">
      {loading && <p role="status"><LoaderCircle size={15} className="spin" aria-hidden />{data ? '刷新中（显示上次结果）' : '正在读取服务端入库记录…'}</p>}
      {error && <div role="alert" className="mg-form-error">
        <p>{data ? '刷新失败，显示上次成功读取的旧数据。' : '读取服务端入库记录失败。'} {error.message}</p>
        {errorHint && <p>{errorHint}</p>}
        <p>读取错误码：{error.code ?? '—'} · 读取请求 trace：{error.traceId ?? '—'}</p>
        {error.detail && <p>{error.detail}</p>}
        {error.errors && <pre>{JSON.stringify(error.errors, null, 2)}</pre>}
        <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={history.refresh}>重试读取</button>
        {error.status === 422 && query.view !== 'all' && <button type="button" className="mg-btn mg-secondary" onClick={() => history.filter('all')}>回到全部</button>}
      </div>}
      {history.changed && <p role="status">记录已变化，请刷新</p>}
    </div>
    {data && <section className="mg-panel" aria-label="当前页入库记录" aria-busy={loading}>
      {data.items.length > 0 ? <>
        <div className="index-tasks-head" aria-hidden="true"><span>文件名</span><span>状态</span><span>是否新图</span><span>耗时</span><span>记录时间（MST）</span><span>详情</span></div>
        <div role="list">{data.items.map(task => {
          const deleted = task.imageState === 'deleted'
          return <article className={`index-tasks-row${deleted ? ' is-image-deleted' : ''}`} role="listitem" key={task.id}>
            <div className="index-tasks-file"><TaskThumbnail url={task.image?.imageUrl ?? null} id={task.image ? task.imageId : null} deleted={deleted} /><strong title={task.filename}>{task.filename}</strong></div>
            <span className={`mg-job-status ${statusClass(task)}`}>{taskStateText(task)}</span>
            <div className="index-tasks-meta">
              <span><small className="index-tasks-label">是否新图：</small>{taskNewImage(task.newImage)}</span>
              <span><small className="index-tasks-label">耗时：</small>{taskDuration(task.durationMs)}</span>
              <span><small className="index-tasks-label">记录时间：</small>{taskTime(task.createdAt)}</span>
            </div>
            <button type="button" className="mg-text-button index-tasks-detail-button" onClick={() => setSelectedId(task.id)}>查看详情</button>
          </article>
        })}</div>
      </> : !history.changed && <EmptyState icon={ListChecks} title={emptyCopy.title} description={emptyCopy.description}>
        {!allEmpty && query.view !== 'all' && <button type="button" className="mg-btn mg-secondary" onClick={() => history.filter('all')}>查看全部</button>}
      </EmptyState>}
      <div className="mg-pagination">
        <span>当前筛选共 {data.total} 条{loading ? '（上次结果）' : ''}</span>
        <div><span>每页 {TASK_PAGE_SIZE} 条</span><button type="button" aria-label="上一页记录" disabled={loading || query.page <= 1} onClick={() => history.page(query.page - 1)}><ChevronLeft size={16} /></button><span>{query.page} / {pages}</span><button type="button" aria-label="下一页记录" disabled={loading || query.page >= pages || query.page >= 1_000_000} onClick={() => history.page(query.page + 1)}><ChevronRight size={16} /></button></div>
      </div>
    </section>}
    <p className="index-tasks-caption">删除图片不会删除它的入库记录：记录保留并标为「图片已删除」，用来追溯哪天入过哪些文件；不需要时用上方按钮清理，或在详情里删除单条。清理只删记录，不动图片。</p>
    <p className="index-tasks-caption">同步逐文件终态，不是后台任务或实时进度；人工场景调整不计入此记录。读取 trace：{data?.readTraceId ?? '—'}。记录时间为插入时刻（America/Denver）。</p>
    {selected && <TaskDetails task={selected} readTraceId={data?.readTraceId ?? null} onClose={() => setSelectedId(null)} onDelete={() => removeRecord(selected)} />}
    {clearing && counts && <ClearRecordsDialog scope={clearing} count={clearing === 'deleted' ? counts.imageDeleted : counts.failed} onConfirm={() => clear(clearing)} onClose={() => setClearing(null)} />}
  </section>
}
