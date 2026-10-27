import { readableApiError } from './search';

type ObjectValue = Record<string, unknown>;

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : {};
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

function httpUrl(value: unknown): string | null {
  const candidate = text(value);
  if (!candidate) return null;
  try {
    const url = new URL(candidate.startsWith('//') ? `https:${candidate}` : candidate);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export interface IndexedItem {
  id: string;
  qdrantPointId: string | null;
  path: string | null;
  /** Only http(s); local filesystem path is never treated as a URL. */
  url: string | null;
  title: string | null;
  category: string | null;
  tags: string[];
  width: number | null;
  height: number | null;
  status: string | null;
  createdAt: string | null;
  raw: Record<string, unknown>;
}

export interface IndexFailedItem {
  filenameOrPath: string | null;
  error: string | null;
  message: string | null;
}

export interface IndexResponse {
  ok: boolean;
  traceId: string | null;
  indexed: IndexedItem[];
  failed: IndexFailedItem[];
  raw: Record<string, unknown>;
}

function normalizeIndexedRow(raw: ObjectValue): IndexedItem | null {
  const id = text(raw.id) ?? text(raw.qdrant_point_id);
  if (!id) return null;
  const tagsRaw = raw.tags;
  const tags = Array.isArray(tagsRaw)
    ? tagsRaw.map(text).filter((value): value is string => value !== null)
    : [];
  return {
    id,
    qdrantPointId: text(raw.qdrant_point_id) ?? id,
    path: text(raw.path),
    url: httpUrl(raw.url),
    title: text(raw.title),
    category: text(raw.category),
    tags,
    width: typeof raw.width === 'number' && Number.isFinite(raw.width) && raw.width > 0 ? raw.width : null,
    height: typeof raw.height === 'number' && Number.isFinite(raw.height) && raw.height > 0 ? raw.height : null,
    status: text(raw.status),
    createdAt: text(raw.created_at),
    raw,
  };
}

/** Thin adapter for POST /api/index success body (ok / indexed / failed). */
export function normalizeIndexResponse(payload: unknown): IndexResponse {
  const body = object(payload);
  const indexedRows = Array.isArray(body.indexed) ? body.indexed : [];
  const failedRows = Array.isArray(body.failed) ? body.failed : [];
  return {
    ok: body.ok === true || (body.ok !== false && indexedRows.length > 0),
    traceId: text(body.trace_id),
    indexed: indexedRows
      .filter((row): row is ObjectValue => row !== null && typeof row === 'object' && !Array.isArray(row))
      .map(normalizeIndexedRow)
      .filter((row): row is IndexedItem => row !== null),
    failed: failedRows
      .filter((row): row is ObjectValue => row !== null && typeof row === 'object' && !Array.isArray(row))
      .map(row => ({
        filenameOrPath: text(row.filename_or_path) ?? text(row.filename) ?? text(row.path),
        error: text(row.error),
        message: text(row.message) ?? text(row.detail),
      })),
    raw: body,
  };
}

export interface IndexOptions {
  file?: File;
  files?: File[];
  category?: string;
  tags?: string[];
  baseUrl?: string;
  signal?: AbortSignal;
}

/** Index via POST /api/index — multipart `file` or `files` (≤100); optional category/tags. */
export async function indexImage({
  file,
  files,
  category,
  tags,
  baseUrl = import.meta.env.VITE_API_BASE_URL ?? '',
  signal,
}: IndexOptions): Promise<IndexResponse> {
  const batch = files?.length ? files : file ? [file] : [];
  if (!batch.length) throw new Error('请选择至少一张待入库图片。');
  if (batch.length > 100) throw new Error('单次最多入库 100 张图片。');
  for (const item of batch) {
    if (!item.size) throw new Error(`${item.name}：图片文件为空，请重新选择。`);
  }

  const form = new FormData();
  if (batch.length === 1) form.append('file', batch[0]);
  else batch.forEach(item => form.append('files', item));
  if (category && category !== '未分类') form.append('category', category);
  if (tags?.length) form.append('tags', JSON.stringify(tags));

  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/+$/, '')}/api/index`, {
      method: 'POST',
      body: form,
      signal,
    });
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new Error('无法连接入库服务，请确认后端已启动，并检查接口地址与网络连接。');
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (!response.ok) throw new Error(readableApiError(null, response.status, '入库'));
    throw new Error('入库接口未返回有效的 JSON 数据，请检查接口地址与代理配置。');
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status, '入库'));

  const normalized = normalizeIndexResponse(payload);
  // 200 + empty indexed + failed → treat as per-item failure for single-file callers.
  if (!normalized.indexed.length && normalized.failed.length) {
    const first = normalized.failed[0];
    const code = first.error ? `（${first.error}）` : '';
    throw new Error(first.message ? `入库失败${code}：${first.message}` : `入库失败${code || ''}，请重试。`);
  }
  if (!normalized.indexed.length) {
    throw new Error('入库接口未返回已索引条目，请检查后端响应。');
  }
  return normalized;
}
