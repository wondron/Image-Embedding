import type { CategoryFailureView, CategoryTreeNode } from '../../entities/category'

/** Code and trace id of a failure, for the end of a message. */
export function failureSuffix(failure: CategoryFailureView): string {
  const code = failure.code ? `（${failure.code}）` : ''
  const trace = failure.traceId ? ` 追踪号：${failure.traceId}` : ''
  return `${code}${trace}`
}

/** Why the tree could not be loaded. */
export function loadFailureText(failure: CategoryFailureView): string {
  if (failure.kind === 'uncertain' && failure.status === null) return '类别列表请求超时或连接中断，请重试。'
  return failure.detail
}

/** A failed create / edit that is not tied to one field. */
export function saveFailureText(failure: CategoryFailureView): string {
  switch (failure.kind) {
    case 'uncertain':
      return '没有收到明确结果，无法确认是否已保存。左侧的树已刷新，请先核对，再决定是否重新提交。'
    case 'not_found':
      return '这个类别已经不存在，可能已被删除。左侧的树已刷新，请关闭后重新选择。'
    case 'validation':
      return `保存被拒绝：${failure.detail}。类别树可能已有变化，请刷新后重试。`
    default:
      return `${failure.detail}${failureSuffix(failure)}`
  }
}

/** A failed delete. */
export function deleteFailureText(failure: CategoryFailureView): string {
  switch (failure.kind) {
    case 'uncertain':
      return '没有收到明确结果，无法确认是否已删除。类别树已刷新，请核对。'
    case 'in_use':
      return '这个类别正在被使用，不能删除。类别树已刷新，可以查看它的子类别、图片和场景规则数量。'
    default:
      return `${failure.detail}${failureSuffix(failure)}`
  }
}

/** Why a node cannot be deleted (the server refuses the same cases); null when it can. */
export function deleteBlockReason(entry: CategoryTreeNode): string | null {
  const uses: string[] = []
  if (entry.children.length > 0) uses.push(`${entry.children.length} 个子类别`)
  if (entry.node.imageCount > 0) uses.push(`${entry.node.imageCount} 张图片`)
  if (entry.node.sceneRuleCount > 0) uses.push(`${entry.node.sceneRuleCount} 条场景规则`)
  return uses.length > 0 ? `还有 ${uses.join('、')} 在使用这个类别，不能删除；不再使用时可以改为停用。` : null
}
