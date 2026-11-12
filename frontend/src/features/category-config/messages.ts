import type { CategoryFailureView, CategorySceneSync, CategoryTreeNode, CategoryVectorSync } from '../../entities/category'

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
      return '没有收到明确结果，无法确认是否已保存。类别树已刷新，请先核对，再决定是否重新提交。'
    case 'not_found':
      return '这个类别已经不存在，可能已被删除。类别树已刷新，请关闭后重新选择。'
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
      return '这个类别正在被使用，不能删除。类别树已刷新，上面是它最新的使用情况。'
    default:
      return `${failure.detail}${failureSuffix(failure)}`
  }
}

/** What still points at a node, e.g. ["1 个下级类别", "3 张图片"]; the server refuses to delete it while any does. */
export function categoryUses(entry: CategoryTreeNode): string[] {
  const uses: string[] = []
  if (entry.children.length > 0) uses.push(`${entry.children.length} 个下级类别`)
  if (entry.node.imageCount > 0) uses.push(`${entry.node.imageCount} 张图片`)
  if (entry.node.sceneRuleCount > 0) uses.push(`${entry.node.sceneRuleCount} 条场景规则`)
  return uses
}

/** Why a node cannot be deleted; null when it can. */
export function deleteBlockReason(entry: CategoryTreeNode): string | null {
  const uses = categoryUses(entry)
  return uses.length > 0 ? `还有 ${uses.join('、')} 在使用这个类别，不能删除。` : null
}

/** How a save is told: plain, or with what a move / cuisines change did to the images below the node. */
export interface SavedNotice {
  type: 'success' | 'warn'
  text: string
  title?: string
}

/** Why changed images are not in the vector store yet, and what happens next. */
const PENDING_REASON: Record<CategoryVectorSync, string> = {
  pushed: '后台会自动补上',
  partial: '后台会自动补上；当前向量库里没有向量的图片，要打开它重新生成向量',
  not_ready: '检索服务未就绪，恢复后后台会自动补上',
  failed: '写入向量库失败，后台会自动重试',
}

/** saved: e.g. 「已保存「披萨」。」; sync: the PATCH's scene_sync (null when nothing could change). */
export function savedNotice(saved: string, sync: CategorySceneSync | null): SavedNotice {
  if (!sync || sync.changed === 0) return { type: 'success', text: saved }
  const updated = `${saved}其下 ${sync.changed} 张图片的场景归属已随之更新`
  if (sync.pending === 0) return { type: 'success', text: `${updated}，并已同步到向量库。` }
  return {
    type: 'warn',
    title: '部分图片还没同步到向量库',
    text: `${updated}，其中 ${sync.pending} 张还没同步到向量库：${PENDING_REASON[sync.vectorSync]}。在此之前，检索可能仍按旧的场景过滤它们；急用可以到「场景管理」全量同步。`,
  }
}
