import type { ImageDeletionFailure, ImageDeletionRun } from '../../entities/task'

/** Rows per POST /api/image-deletions/process, and how many such runs one click may chain. */
export const CLEAN_BATCH = 50
export const CLEAN_MAX_RUNS = 10

/** What one 「立即清理」 did, over every run it chained. */
export interface CleanupTotals {
  runs: number
  /** Cleaned and out of the queue. */
  processed: number
  /** Still queued after the last run that tried them (one entry per image, its latest reason). */
  failed: ImageDeletionFailure[]
  /** Queued after the last run. */
  remaining: number
}

export const NO_CLEANUP: CleanupTotals = { runs: 0, processed: 0, failed: [], remaining: 0 }

/** Add one run. An image that failed before and was cleaned now is no longer a failure. */
export function addRun(totals: CleanupTotals, run: ImageDeletionRun): CleanupTotals {
  const cleaned = new Set(run.processed)
  const failed = new Map(totals.failed.filter(item => !cleaned.has(item.imageId)).map(item => [item.imageId, item]))
  for (const item of run.failed) failed.set(item.imageId, item)
  return { runs: totals.runs + 1, processed: totals.processed + run.processed.length, failed: [...failed.values()], remaining: run.remaining }
}

/**
 * Whether another run may help: something is left, the last run cleaned something (the queue
 * hands out the oldest rows first, so a run that cleaned nothing would only retry the same
 * failures) and the click has runs left.
 */
export function shouldContinue(totals: CleanupTotals, last: ImageDeletionRun): boolean {
  return last.remaining > 0 && last.processed.length > 0 && totals.runs < CLEAN_MAX_RUNS
}

/** The reasons of the failures in words, the most frequent first (at most three). */
export function failureReasons(failed: readonly ImageDeletionFailure[]): string {
  const counts = new Map<string, number>()
  for (const item of failed) counts.set(item.detail, (counts.get(item.detail) ?? 0) + 1)
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])
  const shown = sorted.slice(0, 3).map(([detail, count]) => (failed.length > 1 ? `${detail}（${count} 张）` : detail))
  return sorted.length > 3 ? `${shown.join('；')}等` : shown.join('；')
}

/** How a finished click is told: toast when all went well, popup when something stayed behind. */
export function cleanupNotice(totals: CleanupTotals): { type: 'success' | 'info' | 'warn'; text: string; title?: string } {
  if (totals.failed.length > 0) {
    return {
      type: 'warn',
      title: '部分图片没有清理完',
      text: `已清理 ${totals.processed} 张；${totals.failed.length} 张清理失败，仍留在队列里：${failureReasons(totals.failed)}。后台会定时重试，也可以稍后再点「立即清理」。`,
    }
  }
  if (totals.remaining > 0) {
    return { type: 'info', text: `已清理 ${totals.processed} 张，还有 ${totals.remaining} 张在排队，可以继续清理。` }
  }
  return { type: 'success', text: totals.processed > 0 ? `已清理 ${totals.processed} 张图片的向量和文件，队列已清空。` : '队列里已经没有要清理的图片。' }
}
