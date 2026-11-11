import { useId, useState, type ReactNode } from 'react'
import { IndexTaskHistory } from '../../features/index-tasks'

/** A child of ManagementViews, never a replacement for its File/jobs host. */
export function TasksPage({ apiBase, queue }: { apiBase: string; queue: ReactNode }) {
  const [source, setSource] = useState<'history' | 'queue'>('history')
  const id = useId()
  const choices = [{ key: 'history', label: '服务端入库记录' }, { key: 'queue', label: '本会话上传队列' }] as const
  return <section aria-label="入库任务数据源">
    <div className="mg-page-toolbar"><div className="mg-tabs index-task-source-tabs" role="tablist" aria-label="任务数据源">
      {choices.map((choice, index) => <button type="button" key={choice.key} id={`${id}-${choice.key}`} role="tab" aria-selected={source === choice.key} aria-controls={`${id}-panel`} tabIndex={source === choice.key ? 0 : -1} className={source === choice.key ? 'active' : ''} onClick={() => setSource(choice.key)} onKeyDown={event => {
        const next = event.key === 'ArrowRight' || event.key === 'ArrowLeft' ? 1 - index : event.key === 'Home' ? 0 : event.key === 'End' ? 1 : null
        if (next === null) return
        event.preventDefault(); setSource(choices[next].key)
        document.getElementById(`${id}-${choices[next].key}`)?.focus()
      }}>{choice.label}</button>)}
    </div></div>
    <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${source}`}>
      {source === 'history' ? <IndexTaskHistory key={apiBase} apiBase={apiBase} /> : queue}
    </div>
  </section>
}
