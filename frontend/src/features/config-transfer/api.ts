import { ApiError, requestJsonResult, type ApiLabels } from '../../shared/api'
import { toImportReport, type ImportReport } from './model'

const CATEGORY_LABELS: ApiLabels = { service: '类别服务', endpoint: '类别导入接口', action: '类别树导入' }
const SCENE_LABELS: ApiLabels = { service: '场景服务', endpoint: '场景导入接口', action: '场景配置导入' }

/** A real import writes item by item; a few hundred items take a while. */
const IMPORT_TIMEOUT_MS = 300_000

export interface ImportOptions {
  baseUrl?: string
  /** true: only say what would happen; false: write. */
  dryRun: boolean
  signal?: AbortSignal
}

async function postImport(
  path: string, document: unknown, query: URLSearchParams, labels: ApiLabels, { baseUrl, signal }: ImportOptions,
): Promise<ImportReport> {
  const result = await requestJsonResult(path, {
    baseUrl, method: 'POST', query, json: document, signal: signal ?? AbortSignal.timeout(IMPORT_TIMEOUT_MS), labels,
  })
  const report = toImportReport(result.body, result.traceId)
  if (!report) {
    throw new ApiError(`${labels.endpoint}的响应不符合约定，请刷新页面核对后再操作。`, { status: result.status, traceId: result.traceId })
  }
  return report
}

/**
 * POST /api/categories/import?dry_run=…: the body is the file's content (the downloaded category
 * tree, or GET /api/categories' body). Upsert by code; nothing is moved or deleted (§6.8).
 */
export function importCategoryTree(document: unknown, options: ImportOptions): Promise<ImportReport> {
  return postImport('/api/categories/import', document, new URLSearchParams({ dry_run: String(options.dryRun) }), CATEGORY_LABELS, options)
}

/**
 * POST /api/scenes/import?dry_run=…&prune=…: the body is the file's content (the downloaded scene
 * configuration, or GET /api/scenes' body). prune: in the scenes the file lists, also delete the
 * rules and labels it does not list (§6.8).
 */
export function importSceneConfig(document: unknown, options: ImportOptions & { prune?: boolean }): Promise<ImportReport> {
  const query = new URLSearchParams({ dry_run: String(options.dryRun), prune: String(options.prune === true) })
  return postImport('/api/scenes/import', document, query, SCENE_LABELS, options)
}
