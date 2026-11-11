/**
 * The vector store side of one image, in words: what the search filters read from its point
 * (category, scenes, food state, label status) next to what PostgreSQL says now.
 */
import { payloadDifferences, type ImageVectorState, type VectorStatus } from '../../entities/image'

export const PAYLOAD_FIELD_LABELS: Readonly<Record<string, string>> = {
  category_id: '类别',
  scenes: '所属场景',
  food_state: '食物状态',
  label_status: '标注状态',
}

export const VECTOR_STATUS_TEXT: Readonly<Record<VectorStatus, { label: string; help: string }>> = {
  synced: { label: '已同步', help: '向量库和数据库一致：检索按这里的类别、场景、食物状态和标注状态过滤这张图。' },
  stale: { label: '待同步', help: '向量库里还是旧的过滤信息，检索可能按旧的类别或场景命中这张图。' },
  missing: { label: '没有向量', help: '当前向量库里没有这张图，检索查不到它。可以用存储里的原图重新生成向量，标注不变；原图读不到时，重新上传同一张图片也能补上。' },
  unavailable: { label: '无法读取', help: '' },
}

/** How to name the values a payload holds. */
export interface PayloadNames {
  /** A category id → its path, e.g. 「食材 / 蔬菜 / 土豆」; null when the tree does not have it. */
  category: (id: number) => string | null
  scene: (code: string) => string
  foodState: (value: string) => string
  labelStatus: (value: string) => string
}

/** One payload value in words: category path, scene names, enum labels. */
export function describePayloadValue(key: string, value: unknown, names: PayloadNames): string {
  const empty = value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0)
  if (empty) return key === 'category_id' ? '未设类别' : key === 'scenes' ? '无' : '未填写'
  switch (key) {
    case 'category_id':
      return typeof value === 'number' ? names.category(value) ?? `类别 #${value}` : String(value)
    case 'scenes':
      return Array.isArray(value) ? value.map(code => names.scene(String(code))).join('、') : String(value)
    case 'food_state':
      return names.foodState(String(value))
    case 'label_status':
      return names.labelStatus(String(value))
    default:
      return Array.isArray(value) ? value.map(String).join('、') : typeof value === 'object' ? JSON.stringify(value) : String(value)
  }
}

export interface PayloadDiffRow {
  key: string
  label: string
  /** In the vector store now. */
  stored: string
  /** What PostgreSQL gives (what a sync writes). */
  expected: string
}

/** The fields where the point lags PostgreSQL, in words. */
export function payloadDiffRows(vector: Pick<ImageVectorState, 'payload' | 'expectedPayload'>, names: PayloadNames): PayloadDiffRow[] {
  return payloadDifferences(vector).map(([key, stored, expected]) => ({
    key,
    label: PAYLOAD_FIELD_LABELS[key] ?? key,
    stored: describePayloadValue(key, stored, names),
    expected: describePayloadValue(key, expected, names),
  }))
}

/** Why the state could not be read, in words; the server's own detail goes to the technical info. */
export function unavailableReason(detail: string | null): string {
  const text = (detail ?? '').toLowerCase()
  if (!text || /retrieval stack|not ready|not initiali[sz]ed|embedding model|encoder/.test(text)) {
    return '检索服务没有就绪（编码模型或向量库未启动），暂时读不到这张图在向量库里的状态。数据库里的标注照常可以修改，检索服务恢复后再同步。'
  }
  return '读取向量库失败，可以稍后刷新再看。'
}
