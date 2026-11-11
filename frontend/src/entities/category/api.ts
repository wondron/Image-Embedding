import {
  ApiError,
  requestJsonResult,
  type ApiLabels,
  type CategoryCreateRequestWire,
  type CategoryUpdateRequestWire,
} from '../../shared/api'
import { toCategoryDetail, toCategoryList, toDeletedCategoryId, type CategoryListResult, type CategoryNode } from './model'

const LABELS: ApiLabels = { service: '类别服务', endpoint: '类别接口', action: '类别管理' }

export interface CategoryRequestOptions {
  baseUrl?: string
  signal?: AbortSignal
}

/**
 * A write the server accepted (any 2xx). `node` is null when the body could not be read as the
 * contract's shape: the write still happened, and the caller reloads the tree to see it.
 */
export interface CategoryWriteResult {
  node: CategoryNode | null
  traceId: string | null
}

function categoryPath(id: number): string {
  return `/api/categories/${encodeURIComponent(String(id))}`
}

/** A 2xx whose body was not JSON still means the server did the write. */
function acceptedWithoutBody(error: unknown): CategoryWriteResult | null {
  if (error instanceof ApiError && error.status !== null && error.status >= 200 && error.status < 300) {
    return { node: null, traceId: error.traceId }
  }
  return null
}

/** GET /api/categories: the whole tree, the heating groups and the root codes. */
export async function listCategories({ baseUrl, signal }: CategoryRequestOptions = {}): Promise<CategoryListResult> {
  const result = await requestJsonResult('/api/categories', { baseUrl, signal, labels: LABELS })
  const parsed = toCategoryList(result.body, result.traceId)
  if (!parsed) {
    throw new ApiError('类别接口未返回有效的类别列表。', { status: result.status, traceId: result.traceId })
  }
  return parsed
}

/** POST /api/categories. Does not retry. */
export async function createCategory(
  body: CategoryCreateRequestWire,
  { baseUrl, signal }: CategoryRequestOptions = {},
): Promise<CategoryWriteResult> {
  try {
    const result = await requestJsonResult('/api/categories', { baseUrl, method: 'POST', json: body, signal, labels: LABELS })
    return { node: toCategoryDetail(result.body), traceId: result.traceId }
  } catch (error) {
    const accepted = acceptedWithoutBody(error)
    if (accepted) return accepted
    throw error
  }
}

/** PATCH /api/categories/{id} with only the fields to change. Does not retry. */
export async function updateCategory(
  id: number,
  body: CategoryUpdateRequestWire,
  { baseUrl, signal }: CategoryRequestOptions = {},
): Promise<CategoryWriteResult> {
  try {
    const result = await requestJsonResult(categoryPath(id), { baseUrl, method: 'PATCH', json: body, signal, labels: LABELS })
    return { node: toCategoryDetail(result.body), traceId: result.traceId }
  } catch (error) {
    const accepted = acceptedWithoutBody(error)
    if (accepted) return accepted
    throw error
  }
}

/** DELETE /api/categories/{id}; resolves once the server accepted it. Does not retry. */
export async function deleteCategory(
  id: number,
  { baseUrl, signal }: CategoryRequestOptions = {},
): Promise<{ deletedId: number | null; traceId: string | null }> {
  try {
    const result = await requestJsonResult(categoryPath(id), { baseUrl, method: 'DELETE', signal, labels: LABELS })
    return { deletedId: toDeletedCategoryId(result.body), traceId: result.traceId }
  } catch (error) {
    const accepted = acceptedWithoutBody(error)
    if (accepted) return { deletedId: null, traceId: accepted.traceId }
    throw error
  }
}
