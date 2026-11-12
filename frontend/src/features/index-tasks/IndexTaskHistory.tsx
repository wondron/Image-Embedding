import { useEffect, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, ListChecks, LoaderCircle, RefreshCw, XCircle } from 'lucide-react'
import { EmptyState, Metric, notify } from '../../shared/ui'
import type { IndexTaskStatus } from '../../entities/task'
import { TaskDetails, TaskThumbnail } from './TaskDetails'
import { taskDuration, taskNewImage, taskTime } from './presentation'
import { TASK_PAGE_SIZE, useIndexTaskHistory } from './useIndexTaskHistory'
import './index-tasks.css'

const filters: { status: IndexTaskStatus | null; label: string }[] = [
  { status: null, label: '全部' }, { status: 'succeeded', label: '成功' }, { status: 'failed', label: '失败' },
]
export function IndexTaskHistory({ apiBase }: { apiBase: string }) {
  const history = useIndexTaskHistory(apiBase)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const { data, loading, error, query } = history
  const selected = data?.items.find(item => item.id === selectedId)
  useEffect(() => { setSelectedId(null) }, [history.key])
  useEffect(() => {
    if (selectedId !== null && data && !loading && !error && !data.items.some(item => item.id === selectedId)) {
      setSelectedId(null); notify.info('该记录不在当前页，请重新选择。')
    }
  }, [selectedId, data, loading, error])
  const pages = data ? Math.max(1, Math.ceil(data.total / TASK_PAGE_SIZE)) : 1
  const allEmpty = data?.counts.succeeded === 0 && data?.counts.failed === 0
  const errorHint = error?.status === 404 ? '请核对运行版本或代理路径；此响应不表示源码没有接口。'
    : error?.code === 'NOT_READY' ? '任务目录服务尚未就绪，不代表暂无记录。'
    : error?.status === 422 ? '查询参数不合法，可回到默认筛选再试。' : ''
  return <section className="index-tasks" aria-label="服务端入库记录">
    <div className="mg-metrics">
      <Metric label="成功记录" value={data ? String(data.counts.succeeded) : '—'} detail="全历史记录，不是新增图片数" icon={Check} />
      <Metric label="失败记录" value={data ? String(data.counts.failed) : '—'} detail="全历史记录，不受当前筛选影响" icon={XCircle} />
      <Metric label="记录合计" value={data ? String(BigInt(data.counts.succeeded) + BigInt(data.counts.failed)) : '—'} detail="两态记录合计，不含本地队列" icon={ListChecks} />
    </div>
    <div className="mg-page-toolbar">
      <div className="mg-tabs" role="group" aria-label="历史结果筛选">{filters.map(filter => <button type="button" key={filter.status ?? 'all'} className={query.status === filter.status ? 'active' : ''} aria-pressed={query.status === filter.status} onClick={() => history.filter(filter.status)}>{filter.label}</button>)}</div>
      <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={history.refresh}><RefreshCw size={15} />刷新</button>
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
        {error.status === 422 && query.status !== null && <button type="button" className="mg-btn mg-secondary" onClick={() => history.filter(null)}>回到全部</button>}
      </div>}
      {history.changed && <p role="status">记录已变化，请刷新</p>}
    </div>
    {data && <section className="mg-panel" aria-label="当前页入库记录" aria-busy={loading}>
      {data.items.length > 0 ? <>
        <div className="index-tasks-head" aria-hidden="true"><span>文件名</span><span>终态</span><span>是否新图</span><span>耗时</span><span>记录时间（MST）</span><span>详情</span></div>
        <div role="list">{data.items.map(task => <article className="index-tasks-row" role="listitem" key={task.id}>
          <div className="index-tasks-file"><TaskThumbnail url={task.image?.imageUrl ?? null} /><strong title={task.filename}>{task.filename}</strong></div>
          <span className={`mg-job-status mg-job-status-${task.status === 'succeeded' ? 'success' : 'error'}`}>{task.status === 'succeeded' ? '成功' : '失败'}</span>
          <div className="index-tasks-meta">
            <span><small className="index-tasks-label">是否新图：</small>{taskNewImage(task.newImage)}</span>
            <span><small className="index-tasks-label">耗时：</small>{taskDuration(task.durationMs)}</span>
            <span><small className="index-tasks-label">记录时间：</small>{taskTime(task.createdAt)}</span>
          </div>
          <button type="button" className="mg-text-button index-tasks-detail-button" onClick={() => setSelectedId(task.id)}>查看详情</button>
        </article>)}</div>
      </> : !history.changed && <EmptyState icon={ListChecks} title={allEmpty ? '暂无服务端入库记录' : '当前筛选无记录'} description={allEmpty ? '这里只含接入后实际记录的逐文件结果；写记录可能缺行，不表示图库没有图片，也不承诺永久保留。' : '全历史仍有记录，当前结果筛选没有匹配项。'}>
        {!allEmpty && query.status !== null && <button type="button" className="mg-btn mg-secondary" onClick={() => history.filter(null)}>查看全部</button>}
      </EmptyState>}
      <div className="mg-pagination">
        <span>当前筛选共 {data.total} 条{loading ? '（上次结果）' : ''}</span>
        <div><span>每页 {TASK_PAGE_SIZE} 条</span><button type="button" aria-label="上一页记录" disabled={loading || query.page <= 1} onClick={() => history.page(query.page - 1)}><ChevronLeft size={16} /></button><span>{query.page} / {pages}</span><button type="button" aria-label="下一页记录" disabled={loading || query.page >= pages || query.page >= 1_000_000} onClick={() => history.page(query.page + 1)}><ChevronRight size={16} /></button></div>
      </div>
    </section>}
    <p className="index-tasks-caption">同步逐文件终态，不是后台任务或实时进度；人工场景调整不计入此记录。读取 trace：{data?.readTraceId ?? '—'}。记录时间为插入时刻（America/Denver）。</p>
    {selected && <TaskDetails task={selected} readTraceId={data?.readTraceId ?? null} onClose={() => setSelectedId(null)} />}
  </section>
}
