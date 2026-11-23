import { ApiError, requestJsonResult, type ApiLabels } from '../../shared/api'
import {
  toDetectionResult, toRecipeResult, toSegmentResult, type DetectionResult, type RecipeResult, type SegmentResult,
} from './model'

const label = (action: string): ApiLabels => ({ service: '模型工具服务', endpoint: '模型工具接口', action })
const DETECT = label('目标检测')
const SEGMENT = label('图像分割')
const RECIPE = label('菜谱识别')

/** How many close dishes the page lists per box (the server allows 1–10). */
export const RECIPE_TOP_K = 5

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

export interface RecipeParams {
  /** text_range: one caption; null: AUTO_SEARCH_REGION_CONFIG.caption (what 业务搜图 detects). */
  caption?: string | null
  /** null: AUTO_SEARCH_REGION_CONFIG.box_threshold. */
  boxThreshold?: number | null
  /** null: AUTO_SEARCH_REGION_CONFIG.text_threshold. */
  textThreshold?: number | null
  /** null: AUTO_SEARCH_REGION_CONFIG.max_regions. */
  maxRegions?: number | null
}

/**
 * POST /api/tools/recipe: 业务搜图's 菜谱识别 on the image, step by step — its boxes, each crop's closest
 * dishes and the whole image's. The threshold is not sent: the response says the configured one
 * (RECIPE_DETECT_CONFIG.threshold) and the page tries others on the scores it has.
 */
export async function recognizeRecipes(
  file: File,
  { caption = null, boxThreshold = null, textThreshold = null, maxRegions = null }: RecipeParams = {},
  { baseUrl, signal }: ToolRequestOptions = {},
): Promise<RecipeResult> {
  const query = new URLSearchParams({ top_k: String(RECIPE_TOP_K) })
  if (caption) query.set('text_range', caption)
  if (boxThreshold !== null) query.set('box_threshold', String(boxThreshold))
  if (textThreshold !== null) query.set('text_threshold', String(textThreshold))
  if (maxRegions !== null) query.set('max_regions', String(maxRegions))
  const result = await requestJsonResult('/api/tools/recipe', { baseUrl, signal, method: 'POST', query, form: imageForm(file), labels: RECIPE })
  const recipe = toRecipeResult(result.body)
  if (!recipe) throw unreadable(result.status, result.traceId)
  return recipe
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
