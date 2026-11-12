import { Images, LoaderCircle } from 'lucide-react'
import type { LibraryImageItem } from '../../entities/image'
import { libraryTitle } from '../library-list'
import { tallyOutcomes } from './runBatch'
import type { BatchRun } from './useBatchRun'

const STRIP_SHOWN = 12

/** The images a batch is about: the first few thumbnails, and how many more. */
export function SelectionStrip({ items }: { items: readonly LibraryImageItem[] }) {
  return <ul className="lb-strip" aria-label={`选中的 ${items.length} 张图片`}>
    {items.slice(0, STRIP_SHOWN).map(item => <li key={item.id} title={libraryTitle(item)}>
      {item.imageUrl ? <img src={item.imageUrl} alt="" loading="lazy" /> : <Images size={16} aria-hidden="true" />}
    </li>)}
    {items.length > STRIP_SHOWN && <li className="lb-strip-more">+{items.length - STRIP_SHOWN}</li>}
  </ul>
}

/**
 * How a batch goes: done / total, the counts, and every image that failed, was skipped or has a
 * note, with the reason (failures first).
 */
export function BatchProgress({ run, verb }: { run: BatchRun; verb: string }) {
  const total = run.items.length
  const reached = run.outcomes.size
  const tally = tallyOutcomes(run.outcomes.values())
  const notReached = total - reached
  const working = run.phase === 'running' || run.phase === 'stopping'
  const rank = { failed: 0, skipped: 1, done: 2 } as const
  const rows = run.items
    .flatMap(item => {
      const outcome = run.outcomes.get(item.id)
      if (!outcome || (outcome.status === 'done' && !outcome.note)) return []
      return [{ item, outcome, text: outcome.status === 'done' ? outcome.note! : outcome.reason }]
    })
    .sort((a, b) => rank[a.outcome.status] - rank[b.outcome.status])
  const title = run.phase === 'running' ? `正在${verb}…`
    : run.phase === 'stopping' ? '正在停止：等已发出的请求完成…'
    : notReached > 0 ? `已停止，还有 ${notReached} 张没有处理`
    : tally.failed > 0 ? `${verb}完成，有 ${tally.failed} 张没成功` : `${verb}完成`

  return <section className="lb-progress" aria-label={`批量${verb}进度`} aria-busy={working}>
    <div className="lb-progress-head">
      <strong role="status">{working && <LoaderCircle size={15} className="spin" />}{title}</strong>
      <span>{reached} / {total}</span>
    </div>
    <progress max={Math.max(total, 1)} value={reached} aria-label={`批量${verb}进度`} />
    <p className="lb-tally">
      <span className="is-done">成功 {tally.done}</span>
      {tally.skipped > 0 && <span className="is-skipped">跳过 {tally.skipped}</span>}
      {tally.failed > 0 && <span className="is-failed">失败 {tally.failed}</span>}
      {!working && notReached > 0 && <span>未处理 {notReached}</span>}
    </p>
    {rows.length > 0 && <ul className="lb-results" aria-label="需要留意的图片">
      {rows.map(({ item, outcome, text }) => <li key={item.id} className={`is-${outcome.status}`}>
        {item.imageUrl ? <img src={item.imageUrl} alt="" loading="lazy" /> : <span className="lb-thumb-empty"><Images size={14} /></span>}
        <span><strong title={item.id}>{libraryTitle(item)}</strong><small>{outcome.status === 'failed' ? '失败' : outcome.status === 'skipped' ? '跳过' : '备注'}：{text}</small></span>
      </li>)}
    </ul>}
  </section>
}
