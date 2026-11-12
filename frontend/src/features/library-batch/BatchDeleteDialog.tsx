import { useState } from 'react'
import { RotateCcw, Square, Trash2 } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import type { LibraryImageItem } from '../../entities/image'
import { notify } from '../../shared/ui'
import { deleteOne } from './batchActions'
import { BatchProgress, SelectionStrip } from './BatchProgress'
import { tallyOutcomes, type BatchOutcome } from './runBatch'
import { useBatchRun } from './useBatchRun'
import './library-batch.css'

/** From this many images on, the person types 「删除」 to confirm. */
export const TYPED_CONFIRM_FROM = 20
const CONFIRM_WORD = '删除'

export interface BatchDeleteDialogProps {
  items: readonly LibraryImageItem[]
  apiBase: string
  onClose: () => void
  /** After each run: some images are gone, what shows them reloads. */
  onFinished: (outcomes: ReadonlyMap<string, BatchOutcome>) => void
}

/** 批量删除: confirm (typed for a large batch), then delete image by image with the progress shown. */
export function BatchDeleteDialog({ items, apiBase, onClose, onFinished }: BatchDeleteDialogProps) {
  const [typed, setTyped] = useState('')
  const { run, start, stop } = useBatchRun()
  const working = run.phase === 'running' || run.phase === 'stopping'
  const failed = run.items.filter(item => run.outcomes.get(item.id)?.status === 'failed')
  const notReached = run.items.filter(item => !run.outcomes.has(item.id))
  const needsTyping = items.length >= TYPED_CONFIRM_FROM
  const confirmed = !needsTyping || typed.trim() === CONFIRM_WORD

  async function begin(targets: readonly LibraryImageItem[]) {
    const outcomes = await start(items, targets, (item, signal) => deleteOne(item, apiBase, signal))
    onFinished(outcomes)
    const tally = tallyOutcomes(outcomes.values())
    if (outcomes.size === items.length && tally.done === items.length) {
      notify.success(`已删除 ${items.length} 张图片。`)
      onClose()
    }
  }

  const footer = <div className="lb-footer">
    {run.phase === 'idle'
      ? <>
        <button type="button" className="mg-btn mg-secondary" data-dialog-initial-focus onClick={onClose}>取消</button>
        <button type="button" className="mg-btn lb-danger" disabled={!confirmed} onClick={() => void begin(items)}><Trash2 size={15} />删除 {items.length} 张</button>
      </>
      : working
        ? <button type="button" className="mg-btn mg-secondary" disabled={run.phase === 'stopping'} onClick={stop}><Square size={14} />{run.phase === 'stopping' ? '正在停止…' : '停止'}</button>
        : <>
          {failed.length > 0 && <button type="button" className="mg-btn mg-secondary" onClick={() => void begin(failed)}><RotateCcw size={15} />重试失败的（{failed.length}）</button>}
          {notReached.length > 0 && <button type="button" className="mg-btn mg-secondary" onClick={() => void begin(notReached)}>继续删除剩下的（{notReached.length}）</button>}
          <button type="button" className="mg-btn mg-primary" onClick={onClose}>完成</button>
        </>}
  </div>

  return <Dialog title={`批量删除（${items.length} 张）`} size="sm" onClose={onClose} dismissDisabled={working} footer={footer}>
    <div className="lb-dialog">
      <SelectionStrip items={items} />
      {run.phase !== 'idle'
        ? <BatchProgress run={run} verb="删除" />
        : <div className="lb-confirm">
          <p>将删除选中的 <strong>{items.length}</strong> 张图片：图库和检索结果里都会消失，向量和原图、缩略图一起清理，<strong>无法恢复</strong>。</p>
          {needsTyping && <label className="lb-type-confirm">
            <span>数量较多，请输入「{CONFIRM_WORD}」确认</span>
            <input className="lb-input" value={typed} autoComplete="off" onChange={event => setTyped(event.target.value)} aria-label={`输入「${CONFIRM_WORD}」确认`} />
          </label>}
        </div>}
    </div>
  </Dialog>
}
