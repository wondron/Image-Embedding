import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type CategoryNode } from '../../entities/category'
import { ApiError } from '../../shared/api'
import {
  EMPTY_BATCH_EDIT,
  changedFields,
  describeBatchEdit,
  needsCurrentTags,
  planImagePatch,
  validateBatchEdit,
  type BatchEdit,
} from './batchModel'
import { failureReason, runBatch, tallyOutcomes, type BatchOutcome } from './runBatch'

function node(id: number, name: string, isActive = true): CategoryNode {
  return {
    id, code: `c${id}`, parentId: null, path: String(id), name, names: [], cuisines: [], heatingGroup: null,
    sortOrder: 0, isActive, imageCount: 0, sceneRuleCount: 0, createdAt: null, updatedAt: null,
  }
}
const tree = buildCategoryTree([node(1, '牛排'), node(2, '停用的', false)])
const edit = (patch: Partial<BatchEdit>): BatchEdit => ({ ...EMPTY_BATCH_EDIT, ...patch })
const amounts = (key: keyof BatchEdit['amounts'], mode: 'keep' | 'set' | 'clear', text = '') => ({ ...EMPTY_BATCH_EDIT.amounts, [key]: { mode, text } })
const labelled = { categoryId: 1, tags: ['带骨', '整块'] }
const unlabelled = { categoryId: null, tags: [] }

describe('batch edit', () => {
  it('changes nothing until a field is set, and names what it changes in form order', () => {
    expect(changedFields(EMPTY_BATCH_EDIT)).toEqual([])
    const some = edit({ tags: { mode: 'add', values: ['x'] }, category: { mode: 'clear', categoryId: null }, amounts: amounts('waterMassG', 'clear') })
    expect(describeBatchEdit(some)).toBe('类别、标签、含水质量')
    expect(needsCurrentTags(some)).toBe(true)
    expect(needsCurrentTags(edit({ tags: { mode: 'replace', values: ['x'] } }))).toBe(false)
  })

  it('checks what is set before anything is sent', () => {
    expect(validateBatchEdit(EMPTY_BATCH_EDIT, null)).toEqual({})
    expect(validateBatchEdit(edit({ category: { mode: 'set', categoryId: null } }), tree).category).toContain('请选择新的类别')
    expect(validateBatchEdit(edit({ category: { mode: 'set', categoryId: 2 } }), tree).category).toContain('已停用')
    expect(validateBatchEdit(edit({ category: { mode: 'set', categoryId: 9 } }), tree).category).toContain('不存在')
    expect(validateBatchEdit(edit({ category: { mode: 'set', categoryId: 1 } }), null).category).toContain('未加载')
    expect(validateBatchEdit(edit({ category: { mode: 'clear', categoryId: null }, labelStatus: 'verified' }), tree).labelStatus).toContain('未标注')
    expect(validateBatchEdit(edit({ tags: { mode: 'add', values: [] } }), tree).tags).toContain('至少一个')
    expect(validateBatchEdit(edit({ tags: { mode: 'remove', values: ['长'.repeat(65)] } }), tree).tags).toContain('64')
    expect(validateBatchEdit(edit({ description: { mode: 'set', text: '  ' } }), tree).description).toContain('清空')
    expect(validateBatchEdit(edit({ foodState: { mode: 'set', value: '' } }), tree).foodState).toBe('请选择食物状态。')
    expect(validateBatchEdit(edit({ amounts: amounts('foodCount', 'set', '0') }), tree).foodCount).toBe('请输入正整数。')
    expect(validateBatchEdit(edit({ amounts: amounts('totalMassG', 'set', '') }), tree).totalMassG).toContain('清空')
    expect(validateBatchEdit(edit({ amounts: amounts('waterMassG', 'set', '0') }), tree)).toEqual({})  // water may be 0
    expect(validateBatchEdit(edit({ category: { mode: 'set', categoryId: 1 }, labelStatus: 'auto', tags: { mode: 'replace', values: ['a'] } }), tree)).toEqual({})
  })
})

describe('planImagePatch', () => {
  it('sets or clears the category; a set category is verified by the server unless a status is given', () => {
    expect(planImagePatch(edit({ category: { mode: 'set', categoryId: 1 } }), unlabelled)).toEqual({ kind: 'patch', patch: { category_id: 1 } })
    expect(planImagePatch(edit({ category: { mode: 'set', categoryId: 1 }, labelStatus: 'auto' }), unlabelled)).toEqual({ kind: 'patch', patch: { category_id: 1, label_status: 'auto' } })
    expect(planImagePatch(edit({ category: { mode: 'clear', categoryId: null } }), labelled)).toEqual({ kind: 'patch', patch: { category_id: null } })
  })

  it('sets a label status only where there is a category', () => {
    const verify = edit({ labelStatus: 'verified' })
    expect(planImagePatch(verify, labelled)).toEqual({ kind: 'patch', patch: { label_status: 'verified' } })
    expect(planImagePatch(verify, unlabelled)).toEqual({ kind: 'skip', reason: '没有类别，不能设标注状态' })
    expect(planImagePatch(edit({ labelStatus: 'verified', description: { mode: 'set', text: ' 新描述 ' } }), unlabelled))
      .toEqual({ kind: 'patch', patch: { description: '新描述' }, note: '没有类别，标注状态没改' })
  })

  it('adds, removes, replaces or clears tags from what the image has', () => {
    expect(planImagePatch(edit({ tags: { mode: 'add', values: ['整块', '红烧'] } }), labelled)).toEqual({ kind: 'patch', patch: { tags: ['带骨', '整块', '红烧'] } })
    expect(planImagePatch(edit({ tags: { mode: 'remove', values: ['带骨'] } }), labelled)).toEqual({ kind: 'patch', patch: { tags: ['整块'] } })
    expect(planImagePatch(edit({ tags: { mode: 'replace', values: ['a', 'a', 'b'] } }), labelled)).toEqual({ kind: 'patch', patch: { tags: ['a', 'b'] } })
    expect(planImagePatch(edit({ tags: { mode: 'clear', values: [] } }), labelled)).toEqual({ kind: 'patch', patch: { tags: [] } })
    // Nothing would change: nothing is sent
    expect(planImagePatch(edit({ tags: { mode: 'add', values: ['带骨'] } }), labelled)).toEqual({ kind: 'skip', reason: '已经是这样了，不用改' })
    expect(planImagePatch(edit({ tags: { mode: 'clear', values: [] } }), unlabelled).kind).toBe('skip')
    const full = { categoryId: 1, tags: Array.from({ length: 32 }, (_, index) => `t${index}`) }
    expect(planImagePatch(edit({ tags: { mode: 'add', values: ['one-more'] } }), full)).toEqual({ kind: 'fail', reason: '加上后有 33 个标签，最多 32 个' })
  })

  it('sets or clears the description and the food annotation', () => {
    const all = edit({
      description: { mode: 'clear', text: '' },
      foodState: { mode: 'set', value: 'cooked' },
      foodProcessing: { mode: 'clear', value: '' },
      amounts: { ...EMPTY_BATCH_EDIT.amounts, foodCount: { mode: 'set', text: ' 3 ' }, waterMassG: { mode: 'set', text: '0' }, totalMassG: { mode: 'clear', text: '' } },
    })
    expect(planImagePatch(all, labelled)).toEqual({ kind: 'patch', patch: {
      description: null, food_state: 'cooked', food_processing: null, food_count: 3, total_mass_g: null, water_mass_g: 0,
    } })
  })
})

describe('runBatch', () => {
  const ok: BatchOutcome = { status: 'done' }

  it('works through every item a few at a time; a throw is that item’s failure', async () => {
    let inFlight = 0
    let most = 0
    const seen: number[] = []
    const result = await runBatch([1, 2, 3, 4, 5, 6, 7], async item => {
      inFlight += 1
      most = Math.max(most, inFlight)
      await new Promise(resolve => setTimeout(resolve, 5))
      inFlight -= 1
      if (item === 4) throw new Error('坏了')
      return ok
    }, { concurrency: 3, onResult: (item, outcome) => seen.push(item * (outcome.status === 'done' ? 1 : -1)) })
    expect(result).toEqual({ completed: true })
    expect(most).toBe(3)
    expect([...seen].sort((a, b) => Math.abs(a) - Math.abs(b))).toEqual([1, 2, 3, -4, 5, 6, 7])
  })

  it('stops starting new items when asked; the started ones finish', async () => {
    let stop = false
    const finished: number[] = []
    const result = await runBatch([1, 2, 3, 4, 5], async item => {
      if (item === 2) stop = true
      await new Promise(resolve => setTimeout(resolve, 1))
      return ok
    }, { concurrency: 2, stopped: () => stop, onResult: item => finished.push(item) })
    expect(result.completed).toBe(false)
    expect(finished.sort()).toEqual([1, 2])
  })

  it('counts the outcomes', () => {
    expect(tallyOutcomes([ok, { status: 'skipped', reason: 'x' }, { status: 'failed', reason: 'y' }, ok])).toEqual({ done: 2, skipped: 1, failed: 1 })
  })

  it('says why an image failed, and when the write may still have happened', () => {
    expect(failureReason(new ApiError('gone', { status: 404 }))).toBe('图片已不存在')
    expect(failureReason(new ApiError('类别已停用', { status: 422, code: 'VALIDATION_ERROR', traceId: 't1' }))).toBe('类别已停用（VALIDATION_ERROR · trace_id=t1）')
    expect(failureReason(new ApiError('服务暂不可用', { status: 503 }))).toBe('结果不确定：服务暂不可用，请刷新后核对')
    expect(failureReason(new DOMException('请求超时', 'TimeoutError'))).toBe('结果不确定：请求超时，请刷新后核对')
  })
})
