import { ApiError, requestJsonResult, type ApiLabels } from '../../shared/api'
import type {
  EmbeddingBuildResponseWire,
  EmbeddingModelActivateResponseWire,
  EmbeddingModelResponseWire,
  EmbeddingModelRetireResponseWire,
} from '../../shared/api'
import { asBoolean, asInteger, untrusted } from '../../shared/lib/parse'
import { toEmbeddingModel, toModelBuild, toModelOverview, type EmbeddingModel, type ModelBuild, type ModelOverview } from './model'

const label = (action: string): ApiLabels => ({ service: '模型服务', endpoint: '模型版本接口', action })
const LIST = label('读取模型版本')
const REGISTER = label('登记模型版本')
const BUILD = label('开始生成向量')
const STOP = label('停止生成向量')
const ACTIVATE = label('切换模型版本')
const RETIRE = label('下线模型版本')

/** Name and version labels: letters, digits, '.', '_', '-', starting with a letter or digit (backend model_versions.label). */
export const MODEL_LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export interface ModelRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

function unreadable(status: number, traceId: string | null): ApiError {
  return new ApiError('模型版本接口返回的数据格式不正确，请刷新后重试。', { status, traceId, code: 'MODEL_PROTOCOL_ERROR' })
}

function modelPath(id: number, action = ''): string {
  if (!Number.isSafeInteger(id) || id < 1) throw new Error('模型版本编号不正确。')
  return `/api/embedding-models/${id}${action}`
}

function modelOf(body: unknown, id: number | null, status: number, traceId: string | null): EmbeddingModel {
  const model = toEmbeddingModel(untrusted<EmbeddingModelResponseWire>(body)?.model)
  if (untrusted<EmbeddingModelResponseWire>(body)?.ok !== true || !model || (id !== null && model.id !== id)) throw unreadable(status, traceId)
  return model
}

/** GET /api/embedding-models: every version, the configured encoder, what search uses, the last build. */
export async function listEmbeddingModels({ baseUrl, signal }: ModelRequestOptions = {}): Promise<ModelOverview> {
  const result = await requestJsonResult('/api/embedding-models', { baseUrl, signal, labels: LIST })
  const overview = toModelOverview(result.body)
  if (!overview) throw unreadable(result.status, result.traceId)
  return overview
}

/** POST /api/embedding-models: the configured encoder becomes a new version (building) with its own empty collection. */
export async function registerEmbeddingModel(
  { name, version }: { name?: string | null; version?: string | null },
  { baseUrl, signal }: ModelRequestOptions = {},
): Promise<EmbeddingModel> {
  const json: Record<string, string> = {}
  for (const [key, value] of [['name', name], ['version', version]] as const) {
    const text = value?.trim()
    if (!text) continue
    if (!MODEL_LABEL_PATTERN.test(text)) throw new Error('名称和版本只能用字母、数字、点、下划线和连字符，以字母或数字开头，最多 64 个字符。')
    json[key] = text
  }
  const result = await requestJsonResult('/api/embedding-models', { baseUrl, signal, method: 'POST', json, labels: REGISTER })
  const model = modelOf(result.body, null, result.status, result.traceId)
  if (model.status !== 'building') throw unreadable(result.status, result.traceId)
  return model
}

function buildOf(body: unknown, id: number, status: number, traceId: string | null, allowNone: boolean): ModelBuild | null {
  const raw = untrusted<EmbeddingBuildResponseWire>(body)
  if (raw?.ok !== true) throw unreadable(status, traceId)
  if (raw.build === null && allowNone) return null
  const build = toModelBuild(raw.build)
  if (!build || (!allowNone && build.modelId !== id)) throw unreadable(status, traceId)
  return build
}

/** POST /api/embedding-models/{id}/build: fill the version's collection in the background (GET shows the progress). */
export async function startModelBuild(id: number, { baseUrl, signal }: ModelRequestOptions = {}): Promise<ModelBuild> {
  const result = await requestJsonResult(modelPath(id, '/build'), { baseUrl, signal, method: 'POST', labels: BUILD })
  return buildOf(result.body, id, result.status, result.traceId, false)!
}

/** POST /api/embedding-models/{id}/build/stop: ends after the batch in flight; what was written stays. */
export async function stopModelBuild(id: number, { baseUrl, signal }: ModelRequestOptions = {}): Promise<ModelBuild | null> {
  const result = await requestJsonResult(modelPath(id, '/build/stop'), { baseUrl, signal, method: 'POST', labels: STOP })
  return buildOf(result.body, id, result.status, result.traceId, true)
}

export interface ActivateResult {
  model: EmbeddingModel
  previousId: number | null
  switched: boolean
  /** Images the switch embedded itself while index requests waited. */
  caughtUp: number
  /** Images still without a vector (only when the switch was allowed to leave them). */
  remaining: number
}

/**
 * POST /api/embedding-models/{id}/activate: search and index switch to this version; the
 * previous one stays ready (switching back is a roll back). allowMissing: switch although some
 * images have no vector in it yet (they cannot be found until they get one).
 */
export async function activateEmbeddingModel(id: number, { allowMissing = false }: { allowMissing?: boolean } = {}, { baseUrl, signal }: ModelRequestOptions = {}): Promise<ActivateResult> {
  const result = await requestJsonResult(modelPath(id, '/activate'), {
    baseUrl, signal, method: 'POST', json: { allow_missing: allowMissing }, labels: ACTIVATE,
  })
  const body = untrusted<EmbeddingModelActivateResponseWire>(result.body)
  const model = modelOf(result.body, id, result.status, result.traceId)
  const switched = asBoolean(body?.switched)
  const previousId = body?.previous_id === null ? null : asInteger(body?.previous_id)
  const caughtUp = asInteger(body?.caught_up)
  const remaining = asInteger(body?.remaining)
  if (switched === undefined || model.status !== 'current' || (body?.previous_id !== null && (previousId === null || previousId < 1))
    || caughtUp === null || caughtUp < 0 || remaining === null || remaining < 0) {
    throw unreadable(result.status, result.traceId)
  }
  return { model, previousId, switched, caughtUp, remaining }
}

export interface RetireResult {
  model: EmbeddingModel
  dropped: boolean
  ledgerRows: number
}

/** POST /api/embedding-models/{id}/retire: drops its collection and ledger rows; not the current version. */
export async function retireEmbeddingModel(id: number, { baseUrl, signal }: ModelRequestOptions = {}): Promise<RetireResult> {
  const result = await requestJsonResult(modelPath(id, '/retire'), { baseUrl, signal, method: 'POST', labels: RETIRE })
  const body = untrusted<EmbeddingModelRetireResponseWire>(result.body)
  const model = modelOf(result.body, id, result.status, result.traceId)
  const dropped = asBoolean(body?.dropped)
  const ledgerRows = asInteger(body?.ledger_rows)
  if (dropped === undefined || ledgerRows === null || ledgerRows < 0 || model.status !== 'retired') throw unreadable(result.status, result.traceId)
  return { model, dropped, ledgerRows }
}
