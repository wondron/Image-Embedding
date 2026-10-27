import type { SearchImage, SearchResponse } from '../types';

type ObjectValue = Record<string, unknown>;

function object(value: unknown): ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

/** Only remote HTTP(S) sources may become clickable links or image sources. */
export function safeHttpUrl(value: unknown): string | null {
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

function thumbnailSources(raw: ObjectValue): unknown[] {
  const details = object(raw.details);
  const images = Array.isArray(details.images) ? details.images.map(object) : [];
  const thumbnails = [object(raw.image_url), raw.thumbnail_url, ...images.map(image => image.thumbnail_url)];
  const sizes = ['large', 'medium', 'small'];
  const sources: unknown[] = [];
  // Rank sizes across every thumbnail collection before trying a smaller variant.
  for (const size of sizes) {
    for (const value of thumbnails) {
      sources.push(typeof value === 'string' && size === 'large' ? value : object(value)[size]);
    }
  }
  for (const value of thumbnails) {
    sources.push(...Object.entries(object(value)).filter(([key]) => !sizes.includes(key)).map(([, url]) => url));
  }
  return sources;
}

function uniqueImageUrls(values: unknown[]): string[] {
  return [...new Set(values.map(safeHttpUrl).filter((url): url is string => url !== null))];
}

/** Candidate URLs only: availability is checked when the image is displayed or downloaded. */
export function getImageSources(item: Pick<SearchImage, 'imageUrl' | 'thumbnailUrl' | 'raw'>): string[] {
  return uniqueImageUrls([item.imageUrl, item.raw.image_url, ...thumbnailSources(item.raw), item.thumbnailUrl]);
}

function positiveNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function similarity(value: unknown): number | null {
  // Do not turn missing/null scores into zero or interpret arbitrary strings as scores.
  return typeof value === 'number' && Number.isFinite(value) && value >= -1 && value <= 1
    ? value
    : null;
}

function stableId(raw: ObjectValue, index: number): string {
  const candidate = text(raw.id) ?? text(raw.point_id) ?? text(raw.image_id)
    ?? (typeof raw.id === 'number' && Number.isFinite(raw.id) ? String(raw.id) : null)
    ?? (typeof raw.point_id === 'number' && Number.isFinite(raw.point_id) ? String(raw.point_id) : null)
    ?? (typeof raw.image_id === 'number' && Number.isFinite(raw.image_id) ? String(raw.image_id) : null);
  return candidate ?? `image-${index + 1}`;
}

/** Map contract `source` (self|flyb|empty); absent → undefined so UI never invents a badge. */
export function mapChannelSource(value: unknown): SearchImage['source'] | undefined {
  const label = text(value)?.toLowerCase();
  if (!label) return undefined;
  if (label === 'self' || label === 'local' || label === 'index') return 'self';
  if (label === 'flyb') return 'flyb';
  if (label === 'empty') return 'empty';
  return undefined;
}

function normalizeItem(raw: ObjectValue, index: number, responseSource?: SearchImage['source']): SearchImage {
  const details = object(raw.details);
  const images = Array.isArray(details.images) ? details.images.map(object) : [];
  const primaryImage = images.find(image => safeHttpUrl(image.image_url)) ?? images[0] ?? {};
  // Contract: image_url must be http(s); filesystem `path` is never used as a URL.
  const imageUrl = safeHttpUrl(raw.image_url) ?? safeHttpUrl(primaryImage.image_url);
  const thumbnailUrl = uniqueImageUrls(thumbnailSources(raw))[0] ?? null;
  const site = object(details.site);
  const pageUrl = safeHttpUrl(details.page_url) ?? safeHttpUrl(raw.page_url);
  const channel = mapChannelSource(raw.source) ?? (
    responseSource === 'self' || responseSource === 'flyb' ? responseSource : undefined
  );
  const snippets = Array.isArray(details.text_snippets)
    ? details.text_snippets.map(text).filter((value): value is string => value !== null)
    : Array.isArray(raw.text_snippets)
      ? raw.text_snippets.map(text).filter((value): value is string => value !== null)
      : [];
  return {
    id: stableId(raw, index),
    title: text(raw.title) ?? text(primaryImage.alt) ?? `未命名图片 ${index + 1}`,
    imageUrl,
    thumbnailUrl: thumbnailUrl ?? safeHttpUrl(raw.thumbnail_url),
    similarity: similarity(raw.similarity) ?? similarity(raw.similiar),
    category: text(raw.category) ?? text(details.category),
    pageUrl,
    siteName: text(raw.site_name) ?? text(details.site) ?? text(site.name) ?? text(site.Name)
      ?? text(site.host) ?? text(site.Host) ?? (pageUrl ? new URL(pageUrl).hostname : null),
    snippets,
    width: positiveNumber(raw.width) ?? positiveNumber(primaryImage.width),
    height: positiveNumber(raw.height) ?? positiveNumber(primaryImage.height),
    ...(channel && channel !== 'empty' ? { source: channel } : {}),
    raw,
  };
}

/** Prefer contract `result: []`; empty may use `{ results, status, message }` envelope. */
export function normalizeSearchResponse(payload: unknown): SearchResponse {
  const wrapper = object(payload);
  const result = Array.isArray(payload) ? payload : wrapper.result;
  const envelope = object(result);
  // Prefer array `result`; also accept top-level items/results and empty envelopes.
  const rows = Array.isArray(payload) ? payload
    : Array.isArray(result) ? result
    : Array.isArray(envelope.results) ? envelope.results
    : Array.isArray(wrapper.items) ? wrapper.items
    : Array.isArray(wrapper.results) ? wrapper.results
    : Array.isArray(envelope.items) ? envelope.items
    : null;
  if (!Array.isArray(rows)) {
    throw new Error('搜索接口返回的数据格式不正确，请检查后端响应。');
  }
  const responseSource = mapChannelSource(wrapper.source);
  const status = text(envelope.status) ?? text(wrapper.status) ?? undefined;
  const upstreamError = object(envelope.upstream_error);
  const upstreamMessage = text(upstreamError.message);
  let message: string | undefined;
  if (responseSource === 'empty') {
    message = text(envelope.message) ?? text(wrapper.message)
      ?? '自建库与外搜均未找到相关图片。';
  } else if (status === 'upstream_empty_response' && responseSource !== 'self' && responseSource !== 'flyb') {
    // Legacy FlyB-only empty envelope (no contract source field).
    message = '上游搜索服务暂未返回结果，请稍后重试或更换图片。';
  } else {
    message = upstreamMessage ?? text(envelope.message) ?? text(wrapper.message) ?? undefined;
  }
  const items = rows
    .filter((row): row is ObjectValue => row !== null && typeof row === 'object' && !Array.isArray(row))
    .map((row, index) => normalizeItem(row, index, responseSource));
  return {
    items,
    traceId: text(wrapper.trace_id) ?? text(envelope.request_id) ?? text(envelope.trace_id),
    status,
    message,
    ...(responseSource ? { source: responseSource } : {}),
  };
}

export function readableApiError(payload: unknown, status: number, action = '搜索'): string {
  const body = object(payload);
  const code = text(body.error);
  const detail = body.detail ?? body.message;
  if (Array.isArray(detail)) {
    const errors = detail.map(entry => {
      const value = object(entry);
      const message = text(value.msg);
      if (!message) return null;
      const location = Array.isArray(value.loc)
        ? value.loc.filter(part => typeof part === 'string' || typeof part === 'number').join('.')
        : '';
      return location ? `${location}: ${message}` : message;
    }).filter(Boolean);
    if (errors.length) return `请求参数错误（HTTP ${status}）：${errors.join('；')}`;
  }
  const description = text(detail) ?? text(object(detail).message);
  if (description && code) return `${action}失败（HTTP ${status} · ${code}）：${description}`;
  if (description) return `${action}失败（HTTP ${status}）：${description}`;
  if (code) return `${action}失败（HTTP ${status}）：${code}`;
  if (status === 503) return `${action}服务尚未就绪（HTTP 503），请检查后端服务和模型状态。`;
  if (status === 413) return '图片过大（HTTP 413），请压缩图片后重试。';
  if (status === 504) return `${action}超时（HTTP 504），请稍后重试。`;
  return `${action}失败（HTTP ${status}），请检查后端服务后重试。`;
}

export interface SearchOptions {
  file?: File;
  url?: string;
  baseUrl?: string;
  signal?: AbortSignal;
  /** Local /api/search only (contract optional). */
  topK?: number;
  scoreThreshold?: number | null;
  traceId?: string | null;
}

export async function searchImages({ file, url, baseUrl = import.meta.env.VITE_API_BASE_URL ?? '', signal }: SearchOptions): Promise<SearchResponse> {
  if ((!file && !url?.trim()) || (file && url?.trim())) {
    throw new Error('请选择一张图片，或填写一个图片链接。');
  }
  if (file && !file.size) throw new Error('图片文件为空，请重新选择。');
  const imageUrl = file ? null : safeHttpUrl(url);
  if (!file && !imageUrl) throw new Error('请输入有效的 HTTP 或 HTTPS 图片链接。');
  const endpoint = file ? 'searchImage_file' : 'searchImage_url';
  const body = file ? new FormData() : JSON.stringify({ img_url: imageUrl });
  if (body instanceof FormData && file) body.append('file', file);
  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/+$/, '')}/api/predict/${endpoint}`, {
      method: 'POST',
      body,
      ...(file ? {} : { headers: { 'Content-Type': 'application/json' } }),
      signal,
    });
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new Error('无法连接搜索服务，请确认后端已启动，并检查接口地址与网络连接。');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (!response.ok) throw new Error(readableApiError(null, response.status));
    throw new Error('搜索接口未返回有效的 JSON 数据，请检查接口地址与代理配置。');
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status));
  return normalizeSearchResponse(payload);
}

/** Self-built vector search via POST /api/search (contract: multipart file or JSON { img_url }). */
export async function searchLocalImages({
  file, url, baseUrl = import.meta.env.VITE_API_BASE_URL ?? '', signal, topK, scoreThreshold, traceId,
}: SearchOptions): Promise<SearchResponse> {
  if ((!file && !url?.trim()) || (file && url?.trim())) {
    throw new Error('请选择一张图片，或填写一个图片链接。');
  }
  if (file && !file.size) throw new Error('图片文件为空，请重新选择。');
  const imageUrl = file ? null : safeHttpUrl(url);
  if (!file && !imageUrl) throw new Error('请输入有效的 HTTP 或 HTTPS 图片链接。');

  let body: FormData | string;
  if (file) {
    const form = new FormData();
    form.append('file', file);
    if (topK !== undefined) form.append('top_k', String(topK));
    if (scoreThreshold !== undefined && scoreThreshold !== null) form.append('score_threshold', String(scoreThreshold));
    if (traceId) form.append('trace_id', traceId);
    body = form;
  } else {
    const payload: Record<string, unknown> = { img_url: imageUrl };
    if (topK !== undefined) payload.top_k = topK;
    if (scoreThreshold !== undefined) payload.score_threshold = scoreThreshold;
    if (traceId) payload.trace_id = traceId;
    body = JSON.stringify(payload);
  }

  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/+$/, '')}/api/search`, {
      method: 'POST',
      body,
      ...(file ? {} : { headers: { 'Content-Type': 'application/json' } }),
      signal,
    });
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new Error('无法连接自建检索服务，请确认后端已启动，并检查接口地址与网络连接。');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (!response.ok) throw new Error(readableApiError(null, response.status));
    throw new Error('自建检索接口未返回有效的 JSON 数据，请检查接口地址与代理配置。');
  }
  if (!response.ok) throw new Error(readableApiError(payload, response.status));
  return normalizeSearchResponse(payload);
}

function downloadName(item: SearchImage, type: string, source: string): string {
  const suffixes: Record<string, string> = {
    'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
    'image/gif': 'gif', 'image/avif': 'avif', 'image/bmp': 'bmp', 'image/svg+xml': 'svg',
  };
  const pathExtension = new URL(source).pathname.match(/\.(jpe?g|png|webp|gif|avif|bmp|svg)$/i)?.[1];
  const extension = suffixes[type.toLowerCase()] ?? pathExtension ?? 'img';
  const filename = item.title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/, '').slice(0, 90) || item.id;
  return `${filename}.${extension}`;
}

/** A successful resolution means the browser was asked to save a downloaded blob. */
export async function downloadImage(item: SearchImage, apiBase?: string): Promise<void> {
  const sources = getImageSources(item);
  if (!sources.length) throw new Error('这张图片没有有效的下载地址。');
  let downloaded: { blob: Blob; source: string } | null = null;
  const attempts = sources.flatMap(source => [
    { source, requestUrl: source },
    ...(apiBase !== undefined ? [{ source,
      requestUrl: `${apiBase.replace(/\/+$/, '')}/api/images/download?url=${encodeURIComponent(source)}`,
    }] : []),
  ]);
  for (const { source, requestUrl } of attempts) {
    try {
      const response = await fetch(requestUrl, {
        mode: 'cors', credentials: 'omit', signal: AbortSignal.timeout(30_000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      if (!blob.size || (blob.type && !blob.type.startsWith('image/') && blob.type !== 'application/octet-stream')) {
        throw new Error('源站未返回有效图片');
      }
      downloaded = { blob, source };
      break;
    } catch {
      // Try the configured download service, then the next thumbnail size.
    }
  }
  if (!downloaded) {
    throw new Error('图片下载失败，源站可能限制跨域访问。可点击“打开原图”，在新页面中手动保存。');
  }
  const { blob, source } = downloaded;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = downloadName(item, blob.type, source);
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    // Allow the browser to start the save before revoking the temporary URL.
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}
