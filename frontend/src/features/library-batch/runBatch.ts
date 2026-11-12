import { ApiError } from '../../shared/api'

/** How one image went. */
export type BatchOutcome =
  | { status: 'done'; note?: string }
  | { status: 'skipped'; reason: string }
  | { status: 'failed'; reason: string }

export interface BatchRunOptions<T> {
  /** Requests in flight at once. */
  concurrency?: number
  /** Checked before each item starts: true stops taking new ones; those already started finish. */
  stopped?: () => boolean
  onResult?: (item: T, outcome: BatchOutcome) => void
}

/**
 * `work` for every item, a few at a time; a throw counts as that item's failure. Stopping never
 * cuts a write in half: it only stops starting new ones. `completed`: every item got an outcome.
 */
export async function runBatch<T>(
  items: readonly T[],
  work: (item: T) => Promise<BatchOutcome>,
  { concurrency = 3, stopped = () => false, onResult }: BatchRunOptions<T> = {},
): Promise<{ completed: boolean }> {
  let next = 0
  const lane = async () => {
    while (next < items.length && !stopped()) {
      const item = items[next]
      next += 1
      let outcome: BatchOutcome
      try {
        outcome = await work(item)
      } catch (error) {
        outcome = { status: 'failed', reason: failureReason(error) }
      }
      onResult?.(item, outcome)
    }
  }
  const lanes = Math.max(1, Math.min(Math.floor(concurrency) || 1, items.length))
  await Promise.all(Array.from({ length: lanes }, lane))
  return { completed: next >= items.length }
}

export interface BatchTally {
  done: number
  skipped: number
  failed: number
}

export function tallyOutcomes(outcomes: Iterable<BatchOutcome>): BatchTally {
  const tally: BatchTally = { done: 0, skipped: 0, failed: 0 }
  for (const outcome of outcomes) {
    if (outcome.status === 'done') tally.done += 1
    else if (outcome.status === 'skipped') tally.skipped += 1
    else tally.failed += 1
  }
  return tally
}

/** No answer, a timeout or a 5xx: the write may still have happened. */
export function uncertain(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'TimeoutError') return true
  if (!(error instanceof ApiError)) return !(error instanceof Error && error.name === 'AbortError')
  return error.status === null || error.status >= 500
}

/** Why one image failed, in words (with the code and trace id the server sent). */
export function failureReason(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return '图片已不存在'
  const message = error instanceof Error && error.message ? error.message : '操作失败'
  const extra = error instanceof ApiError
    ? [error.code, error.traceId ? `trace_id=${error.traceId}` : null].filter(Boolean).join(' · ')
    : ''
  const text = extra ? `${message}（${extra}）` : message
  return uncertain(error) ? `结果不确定：${text}，请刷新后核对` : text
}
