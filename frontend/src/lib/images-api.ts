import { readableApiError, safeHttpUrl } from './search';

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

function positiveInt(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return Math.floor(value);
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    const n = Math.floor(Number(value));
    return n >= 0 ? n : fallback;
  }
  return fallback;
}

function positiveNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(text).filter((entry): entry is string => entry !== null);
}

export interface LibraryImageItem {
  id: string;
  /** http(s) only — never filesystem `path`. */
  imageUrl: string | null;
  title: string | null;
  category: string | null;
  tags: string[];
  scenes: string[];
  createdAt: string | null;
  width: number | null;
  height: number | null;
  raw: Record<string, unknown>;
}

export interface ListImagesResponse {
  ok: boolean;
  traceId: string | null;
  scene: string;
  page: number;
  pageSize: number;
  total: number;
  items: LibraryImageItem[];
  raw: Record<string, unknown>;
}

export interface ListImagesOptions {
  page?: number;
  pageSize?: number;
  q?: string | null;
  category?: string | null;
  /** MVP fixed to normal; do not omit to mean "all scenes". */
  scene?: string;
  baseUrl?: string;
  signal?: AbortSignal;
  traceId?: string | null;
}

function normalizeItem(raw: ObjectValue): LibraryImageItem | null {
  const id = text(raw.id) ?? (typeof raw.id === 'number' && Number.isFinite(raw.id) ? String(raw.id) : null);
  if (!id) return null;
  // Contract: <img> uses image_url only; path must never become src.
  const imageUrl = safeHttpUrl(raw.image_url);
  return {
    id,
    imageUrl,
    title: text(raw.title),
    category: text(raw.category),
    tags: stringList(raw.tags),
    scenes: stringList(raw.scenes),
    createdAt: text(raw.created_at),
    width: positiveNumber(raw.width),
    height: positiveNumber(raw.height),
    raw,
  };
}

/** Thin adapter for GET /api/images success body (contract §3.5). */
export function normalizeListImagesResponse(payload: unknown): ListImagesResponse {
  const body = object(payload);
  const rows = Array.isArray(body.items) ? body.items : [];
  return {
    ok: body.ok === true || (body.ok !== false && rows.length >= 0),
    traceId: text(body.trace_id),
    scene: text(body.scene) ?? 'normal',
    page: positiveInt(body.page, 1) || 1,
    pageSize: positiveInt(body.page_size, 24) || 24,
    total: positiveInt(body.total, 0),
    items: rows
      .filter((row): row is ObjectValue => row !== null && typeof row === 'object' && !Array.isArray(row))
      .map(normalizeItem)
      .filter((row): row is LibraryImageItem => row !== null),
    raw: body,
  };
}

/** Build query: omit empty q / 「全部类别」; always page_size=24; scene defaults normal. */
export function buildListImagesQuery({
  page = 1,
  pageSize = 24,
  q,
  category,
  scene = 'normal',
  traceId,
}: Pick<ListImagesOptions, 'page' | 'pageSize' | 'q' | 'category' | 'scene' | 'traceId'>): URLSearchParams {
  const params = new URLSearchParams();
  params.set('page', String(Math.max(1, Math.floor(page) || 1)));
  params.set('page_size', String(pageSize > 0 ? Math.floor(pageSize) : 24));
  params.set('scene', (scene?.trim() || 'normal'));
  const trimmedQ = typeof q === 'string' ? q.trim() : '';
  if (trimmedQ) params.set('q', trimmedQ);
  const trimmedCategory = typeof category === 'string' ? category.trim() : '';
  if (trimmedCategory && trimmedCategory !== '全部类别') params.set('category', trimmedCategory);
  if (traceId?.trim()) params.set('trace_id', traceId.trim());
  return params;
}

/** List library images via GET /api/images (depends on backend §3.5). */
export async function listImages({
  page = 1,
  pageSize = 24,
  q,
  category,
  scene = 'normal',
  baseUrl = import.meta.env.VITE_API_BASE_URL ?? '',
  signal,
  traceId,
}: ListImagesOptions = {}): Promise<ListImagesResponse> {
  const params = buildListImagesQuery({ page, pageSize, q, category, scene, traceId });
  const root = baseUrl.replace(/\/+$/, '');
  const url = `${root}/api/images?${params.toString()}`;

  let response: Response;
  try {
    response = await fetch(url, { method: 'GET', signal });
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new Error('无法连接图库服务，请确认后端已启动，并检查接口地址与网络连接。');
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (!response.ok) throw new Error(readableApiError(null, response.status, '图库列表'));
    throw new Error('图库接口未返回有效的 JSON 数据，请检查接口地址与代理配置。');
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status, '图库列表'));

  return normalizeListImagesResponse(payload);
}
