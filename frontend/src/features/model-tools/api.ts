import { ApiError, requestJsonResult, type ApiLabels } from '../../shared/api'
import { toDetectionResult, toSegmentResult, type DetectionResult, type SegmentResult } from './model'

const label = (action: string): ApiLabels => ({ service: '模型工具服务', endpoint: '模型工具接口', action })
const DETECT = label('目标检测')
const SEGMENT = label('图像分割')

export interface ToolRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

export interface DetectParams {
  /** text_range: one caption (「food . plate」); null: GROUNDINGDINO_CONFIG.caption. */
  caption?: string | null
  /** null: GROUNDINGDINO_CONFIG.box_threshold. */
  boxThreshold?: number | null
  /** null: GROUNDINGDINO_CONFIG.text_threshold. */
  textThreshold?: number | null
}

function unreadable(status: number, traceId: string | null): ApiError {
  return new ApiError('模型工具接口返回的数据格式不正确，请确认后端版本后重试。', { status, traceId, code: 'TOOL_PROTOCOL_ERROR' })
}

function imageForm(file: File): FormData {
  const form = new FormData()
  form.append('file', file, file.name)
  return form
}

/** POST /api/tools/gdino: GroundingDINO's boxes for the caption, in pixels of the image. */
export async function detectObjects(
  file: File,
  { caption = null, boxThreshold = null, textThreshold = null }: DetectParams = {},
  { baseUrl, signal }: ToolRequestOptions = {},
): Promise<DetectionResult> {
  const query = new URLSearchParams()
  if (caption) query.set('text_range', caption)
  if (boxThreshold !== null) query.set('box_threshold', String(boxThreshold))
  if (textThreshold !== null) query.set('text_threshold', String(textThreshold))
  const result = await requestJsonResult('/api/tools/gdino', { baseUrl, signal, method: 'POST', query, form: imageForm(file), labels: DETECT })
  const detection = toDetectionResult(result.body)
  if (!detection) throw unreadable(result.status, result.traceId)
  return detection
}

/**
 * POST /api/tools/segment?output_vis=false: only the mask. The page draws it over the image itself
 * (叠加、轮廓、抠图、并排), so the server neither draws nor encodes an overlay.
 */
export async function segmentImage(file: File, { baseUrl, signal }: ToolRequestOptions = {}): Promise<SegmentResult> {
  const query = new URLSearchParams({ output_vis: 'false' })
  const result = await requestJsonResult('/api/tools/segment', { baseUrl, signal, method: 'POST', query, form: imageForm(file), labels: SEGMENT })
  const segmentation = toSegmentResult(result.body)
  if (!segmentation) throw unreadable(result.status, result.traceId)
  return segmentation
}
