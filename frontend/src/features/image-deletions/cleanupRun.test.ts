import { describe, expect, it } from 'vitest'
import type { ImageDeletionRun } from '../../entities/task'
import { CLEAN_MAX_RUNS, NO_CLEANUP, addRun, cleanupNotice, failureReasons, shouldContinue } from './cleanupRun'

const run = (processed: string[], failed: [string, string][], remaining: number): ImageDeletionRun => ({
  scanned: processed.length + failed.length,
  processed,
  failed: failed.map(([imageId, detail]) => ({ imageId, code: 'INDEX_QDRANT_FAILED', detail })),
  remaining,
})

describe('cleanup runs', () => {
  it('adds runs up, and an image cleaned on a later run is no longer a failure', () => {
    let totals = addRun(NO_CLEANUP, run(['a', 'b'], [['c', '向量库出错']], 5))
    totals = addRun(totals, run(['c', 'd'], [['e', '对象存储出错']], 1))
    expect(totals).toEqual({
      runs: 2, processed: 4, remaining: 1,
      failed: [{ imageId: 'e', code: 'INDEX_QDRANT_FAILED', detail: '对象存储出错' }],
    })
  })

  it('chains another run only while one cleans something and some are left', () => {
    const progress = run(['a'], [], 3)
    expect(shouldContinue(addRun(NO_CLEANUP, progress), progress)).toBe(true)
    const stuck = run([], [['a', 'x']], 3) // the oldest rows keep failing: another run retries the same
    expect(shouldContinue(addRun(NO_CLEANUP, stuck), stuck)).toBe(false)
    const emptied = run(['a'], [], 0)
    expect(shouldContinue(addRun(NO_CLEANUP, emptied), emptied)).toBe(false)
    expect(shouldContinue({ ...NO_CLEANUP, runs: CLEAN_MAX_RUNS }, progress)).toBe(false)
  })

  it('names the most frequent reasons first', () => {
    const failed = addRun(NO_CLEANUP, run([], [['a', '向量库出错'], ['b', '存储出错'], ['c', '向量库出错']], 3)).failed
    expect(failureReasons(failed)).toBe('向量库出错（2 张）；存储出错（1 张）')
    expect(failureReasons(failed.slice(0, 1))).toBe('向量库出错')
  })

  it('tells success, progress and leftovers apart', () => {
    expect(cleanupNotice(addRun(NO_CLEANUP, run(['a', 'b'], [], 0)))).toEqual({ type: 'success', text: '已清理 2 张图片的向量和文件，队列已清空。' })
    expect(cleanupNotice(addRun(NO_CLEANUP, run(['a'], [], 7)))).toMatchObject({ type: 'info', text: '已清理 1 张，还有 7 张在排队，可以继续清理。' })
    const warn = cleanupNotice(addRun(NO_CLEANUP, run(['a'], [['b', '存储出错']], 1)))
    expect(warn).toMatchObject({ type: 'warn', title: '部分图片没有清理完' })
    expect(warn.text).toContain('1 张清理失败，仍留在队列里：存储出错')
  })
})
