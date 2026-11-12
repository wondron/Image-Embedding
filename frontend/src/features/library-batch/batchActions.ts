import { deleteImage, getImage, updateImage, type LibraryImageItem } from '../../entities/image'
import { ApiError } from '../../shared/api'
import { requestSignal } from '../../shared/lib'
import { needsCurrentTags, planImagePatch, type BatchEdit, type ImageState } from './batchModel'
import type { BatchOutcome } from './runBatch'

const REQUEST_TIMEOUT_MS = 30_000

/**
 * One image of a batch edit: PATCH only what the edit sets. Adding or removing tags reads the
 * image's tags first, so tags someone added meanwhile are kept.
 */
export async function editOne(item: LibraryImageItem, edit: BatchEdit, apiBase: string, signal: AbortSignal): Promise<BatchOutcome> {
  let state: ImageState = { categoryId: item.categoryId, tags: item.tags }
  if (needsCurrentTags(edit)) {
    const detail = await getImage(item.id, { baseUrl: apiBase, signal: requestSignal(signal, REQUEST_TIMEOUT_MS) })
    state = { categoryId: detail.image.categoryId, tags: detail.image.tags }
  }
  const plan = planImagePatch(edit, state)
  if (plan.kind === 'skip') return { status: 'skipped', reason: plan.reason }
  if (plan.kind === 'fail') return { status: 'failed', reason: plan.reason }
  const result = await updateImage(item.id, plan.patch, { baseUrl: apiBase, signal: requestSignal(signal, REQUEST_TIMEOUT_MS) })
  const notes = [plan.note, result.vectorSync && result.vectorSync !== 'pushed' ? '已保存，向量库还没同步' : undefined].filter(Boolean)
  return notes.length ? { status: 'done', note: notes.join('；') } : { status: 'done' }
}

/** One image of a batch delete; one that is already gone counts as deleted (a retry after an unclear answer). */
export async function deleteOne(item: LibraryImageItem, apiBase: string, signal: AbortSignal): Promise<BatchOutcome> {
  try {
    const result = await deleteImage(item.id, { baseUrl: apiBase, signal: requestSignal(signal, REQUEST_TIMEOUT_MS) })
    return result.cleanup === 'done' ? { status: 'done' } : { status: 'done', note: '文件稍后由清理任务删除' }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { status: 'done', note: '已经删除过了' }
    throw error
  }
}
