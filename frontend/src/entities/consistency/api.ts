import { ApiError, requestJsonResult, type ApiLabels } from '../../shared/api'
import { toConsistencyResponse, type CheckOptions, type ConsistencyRun } from './model'

const label = (action: string): ApiLabels => ({ service: '核对服务', endpoint: '数据核对接口', action })
const READ = label('读取数据核对')
const START = label('开始数据核对')
const STOP = label('停止数据核对')

export interface ConsistencyRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

function unreadable(status: number, traceId: string | null): ApiError {
  return new ApiError('数据核对接口返回的数据格式不正确，请刷新后重试。', { status, traceId, code: 'CONSISTENCY_PROTOCOL_ERROR' })
}

function runOf(body: unknown, status: number, traceId: string | null): ConsistencyRun | null {
  const run = toConsistencyResponse(body)
  if (run === undefined) throw unreadable(status, traceId)
  return run
}

/** GET /api/consistency: this process's last check (progress while it runs, the report after); null: none yet. */
export async function getConsistencyCheck({ baseUrl, signal }: ConsistencyRequestOptions = {}): Promise<ConsistencyRun | null> {
  const result = await requestJsonResult('/api/consistency', { baseUrl, signal, labels: READ })
  return runOf(result.body, result.status, result.traceId)
}

/** POST /api/consistency/check: start one in the background (CONSISTENCY_CHECK_RUNNING while one runs). */
export async function startConsistencyCheck(options: CheckOptions, { baseUrl, signal }: ConsistencyRequestOptions = {}): Promise<ConsistencyRun> {
  const result = await requestJsonResult('/api/consistency/check', {
    baseUrl, signal, method: 'POST', labels: START,
    json: { vectors: options.vectors, files: options.files, orphans: options.orphans, content: options.content && options.files, encoders: options.encoders },
  })
  const run = runOf(result.body, result.status, result.traceId)
  if (!run) throw unreadable(result.status, result.traceId)
  return run
}

/** POST /api/consistency/check/stop: ends after the batch in flight; what was checked is still reported. */
export async function stopConsistencyCheck({ baseUrl, signal }: ConsistencyRequestOptions = {}): Promise<ConsistencyRun | null> {
  const result = await requestJsonResult('/api/consistency/check/stop', { baseUrl, signal, method: 'POST', labels: STOP })
  return runOf(result.body, result.status, result.traceId)
}
