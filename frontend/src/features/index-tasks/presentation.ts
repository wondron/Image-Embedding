import type { IndexTask } from '../../entities/task'

export function taskTime(value: string | Date | null): string {
  if (value === null) return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return `${date.toLocaleString('zh-CN', { timeZone: 'America/Denver', hour12: false })} MST`
}
export function taskDuration(value: number | null): string { return value === null ? '—' : `${value}ms` }
export function taskNewImage(value: boolean | null): string { return value === null ? '—' : value ? '新图' : '已存在，沿用标注' }

/**
 * What a record is, one of the history's four views (useIndexTaskHistory):
 * stored (the upload stored a new image that is still in the library), repeated (the image was
 * already stored, nothing was added), deleted (its image was deleted since), failed.
 */
export type TaskState = 'stored' | 'repeated' | 'deleted' | 'failed'

export function taskState(task: IndexTask): TaskState {
  if (task.status === 'failed') return 'failed'
  if (task.imageState === 'deleted') return 'deleted'
  return task.newImage === false ? 'repeated' : 'stored'
}

export const TASK_STATE_TEXT: Readonly<Record<TaskState, string>> = {
  stored: '成功', repeated: '重复上传', deleted: '图片已删除', failed: '失败',
}
