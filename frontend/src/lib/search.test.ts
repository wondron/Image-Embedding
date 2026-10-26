import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadImage, getImageSources, normalizeSearchResponse, readableApiError, safeHttpUrl, searchImages } from './search';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('normalizeSearchResponse', () => {
  it('retains real scores and details while finding valid image and large thumbnail URLs', () => {
    const raw = {
      title: '西红柿', image_url: 'javascript:alert(1)', similarity: 0.82, category: '蔬菜',
      details: {
        page_url: 'https://source.example/food', site: { Name: '食材百科' },
        text_snippets: ['新鲜西红柿', null, 12],
        images: [{ image_url: 'https://img.example/a.jpg', thumbnail_url: { large: 'https://img.example/large.jpg' }, width: 600, height: 400 }],
      },
    };
    const response = normalizeSearchResponse({ result: [raw], trace_id: 'trace-1' });
    expect(response.traceId).toBe('trace-1');
    expect(response.items[0]).toMatchObject({
      title: '西红柿', imageUrl: 'https://img.example/a.jpg', thumbnailUrl: 'https://img.example/large.jpg',
      similarity: 0.82, category: '蔬菜', pageUrl: 'https://source.example/food', siteName: '食材百科',
      width: 600, height: 400, snippets: ['新鲜西红柿'], raw,
    });
  });

  it('does not invent scores or infer categories from food titles', () => {
    const items = normalizeSearchResponse({ result: [
      { title: '蔬菜西红柿', similarity: null, similiar: null },
      { similarity: '0.8' }, { similarity: 0 }, { similarity: Number.NaN },
      { similarity: Number.POSITIVE_INFINITY }, { similarity: 80 }, { similiar: 0.7 },
    ] }).items;
    expect(items.map(item => item.similarity)).toEqual([null, null, 0, null, null, null, 0.7]);
    expect(items.every(item => item.category === null)).toBe(true);
    expect(items[0].imageUrl).toBeNull();
  });

  it('supports empty lists and upstream empty envelopes with distinct messaging', () => {
    expect(normalizeSearchResponse({ result: [], trace_id: null }).items).toEqual([]);
    const response = normalizeSearchResponse({
      result: { results: [], status: 'upstream_empty_response', request_id: 'upstream-id', upstream_error: { code: 500, message: 'Empty response' } },
    });
    expect(response.items).toEqual([]);
    expect(response.status).toBe('upstream_empty_response');
    expect(response.message).toContain('上游搜索服务');
    expect(response.traceId).toBe('upstream-id');
  });

  it('discards malformed records and rejects malformed envelopes', () => {
    const response = normalizeSearchResponse({ result: [null, 'bad', [], 1, { details: null }] });
    expect(response.items).toHaveLength(1);
    expect(response.items[0]).toMatchObject({ title: '未命名图片 1', snippets: [], width: null, height: null });
    for (const payload of [null, {}, { result: null }, { result: 'bad' }, { result: { results: {} } }]) {
      expect(() => normalizeSearchResponse(payload)).toThrow('数据格式不正确');
    }
  });

  it('preserves thumbnail-only records and never mutates the original backend record', () => {
    const raw = { details: { images: [{ thumbnail_url: '//img.example/large.jpg', width: -1, height: '400' }] } };
    const item = normalizeSearchResponse({ result: [raw] }).items[0];
    expect(item.imageUrl).toBeNull();
    expect(item.thumbnailUrl).toBe('https://img.example/large.jpg');
    expect(item.width).toBeNull();
    expect(item.height).toBeNull();
    expect(raw).not.toHaveProperty('id');
  });

  it('preserves an upstream large-thumbnail object in the primary image_url field', () => {
    const item = normalizeSearchResponse({ result: [{
      title: '缩略图回退', image_url: { large: 'https://img.example/fallback.webp' },
    }] }).items[0];
    expect(item.imageUrl).toBeNull();
    expect(item.thumbnailUrl).toBe('https://img.example/fallback.webp');
  });
});

describe('safeHttpUrl', () => {
  it('accepts HTTP(S) and rejects unsafe URLs, embedded credentials and malformed values', () => {
    expect(safeHttpUrl(' https://example.com/a.png ')).toBe('https://example.com/a.png');
    expect(safeHttpUrl('http://localhost:8080/a')).toBe('http://localhost:8080/a');
    for (const value of [null, {}, '', '/local', 'bad-url', 'javascript:alert(1)', 'data:image/png;base64,abc', 'file:///c:/a.png', 'https://user:password@example.com/a']) {
      expect(safeHttpUrl(value)).toBeNull();
    }
  });
});

describe('image candidate sources', () => {
  it('ranks sizes globally, keeps the chosen original first, rejects unsafe variants and deduplicates URLs', () => {
    const item = normalizeSearchResponse({ result: [{
      image_url: { large: 'https://img.example/original-large.png', medium: 'https://img.example/shared.png', tiny: 'https://img.example/tiny.png' },
      thumbnail_url: { large: 'javascript:alert(1)', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' },
      details: { images: [
        { image_url: 'https://img.example/original.png', thumbnail_url: { large: 'https://img.example/detail-large.png', medium: 'https://img.example/shared.png', small: 'file:///secret', other: { invalid: 'nested' } } },
        { thumbnail_url: '//img.example/plain.png' },
      ] },
    }] }).items[0];
    expect(getImageSources(item)).toEqual([
      'https://img.example/original.png', 'https://img.example/original-large.png',
      'https://img.example/detail-large.png', 'https://img.example/plain.png',
      'https://img.example/shared.png', 'https://img.example/medium.png',
      'https://img.example/small.png', 'https://img.example/tiny.png',
    ]);
  });

  it('preserves unverified original URLs without fetching, and exposes medium-only thumbnails', () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const raw = { image_url: 'https://img.example/not-yet-checked.png', thumbnail_url: { medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } };
    const item = normalizeSearchResponse({ result: [raw] }).items[0];
    expect(item.imageUrl).toBe(raw.image_url);
    expect(item.thumbnailUrl).toBe('https://img.example/medium.png');
    expect(getImageSources(item)).toEqual([raw.image_url, 'https://img.example/medium.png', 'https://img.example/small.png']);
    expect(fetcher).not.toHaveBeenCalled();
    expect(raw).toEqual({ image_url: 'https://img.example/not-yet-checked.png', thumbnail_url: { medium: 'https://img.example/medium.png', small: 'https://img.example/small.png' } });
  });

  it('retains manually constructed image and thumbnail URLs with empty raw metadata', () => {
    expect(getImageSources({ imageUrl: 'https://img.example/original.png', thumbnailUrl: 'https://img.example/thumbnail.png', raw: {} }))
      .toEqual(['https://img.example/original.png', 'https://img.example/thumbnail.png']);
    expect(getImageSources({ imageUrl: null, thumbnailUrl: 'https://img.example/thumbnail.png', raw: {} }))
      .toEqual(['https://img.example/thumbnail.png']);
  });
});

describe('API requests and failures', () => {
  it('sends URL requests to the configured server and passes cancellation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ result: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const signal = new AbortController().signal;
    await searchImages({ url: 'https://example.com/a.png', baseUrl: 'https://api.example/', signal });
    expect(fetchMock).toHaveBeenCalledWith('https://api.example/api/predict/searchImage_url', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ img_url: 'https://example.com/a.png' }),
      headers: { 'Content-Type': 'application/json' }, signal,
    }));
  });

  it('uploads the file as multipart without manually setting its boundary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ result: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['image-data'], 'photo.png', { type: 'image/png' });
    await searchImages({ file });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/predict/searchImage_file');
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get('file').name).toBe('photo.png');
    expect(options.headers).toBeUndefined();
  });

  it('formats FastAPI validation arrays and HTTP failures for humans', async () => {
    const error = { detail: [{ loc: ['body', 'img_url'], msg: 'Field required', type: 'missing' }] };
    expect(readableApiError(error, 422)).toContain('body.img_url: Field required');
    expect(readableApiError({ detail: 'service unavailable' }, 503)).toContain('service unavailable');
    expect(readableApiError({}, 504)).toContain('超时');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(error), { status: 422 })));
    await expect(searchImages({ url: 'https://example.com/a.png' })).rejects.toThrow('HTTP 422');
  });

  it('distinguishes network failures and invalid responses, preserving abort errors', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(searchImages({ url: 'https://example.com/a.png' })).rejects.toThrow('无法连接搜索服务');
    fetchMock.mockResolvedValueOnce(new Response('<html>proxy fallback</html>', { status: 200 }));
    await expect(searchImages({ url: 'https://example.com/a.png' })).rejects.toThrow('JSON');
    const abort = new DOMException('Aborted', 'AbortError');
    fetchMock.mockRejectedValueOnce(abort);
    await expect(searchImages({ url: 'https://example.com/a.png' })).rejects.toBe(abort);
  });

  it('rejects invalid inputs without making requests', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(searchImages({})).rejects.toThrow('请选择');
    await expect(searchImages({ url: 'javascript:alert(1)' })).rejects.toThrow('HTTP');
    await expect(searchImages({ file: new File([], 'empty.png') })).rejects.toThrow('为空');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('downloads', () => {
  it('tries original then large then medium and stops before requesting small after a successful download', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response('image-data', { headers: { 'Content-Type': 'image/png' } }));
    vi.stubGlobal('fetch', fetcher);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:medium');
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { appendChild: vi.fn() } });
    vi.stubGlobal('window', { setTimeout: vi.fn() });
    const item = normalizeSearchResponse({ result: [{
      image_url: 'https://img.example/original.png', thumbnail_url: {
        large: 'https://img.example/large.png', medium: 'https://img.example/medium.png', small: 'https://img.example/small.png',
      },
    }] }).items[0];
    await downloadImage(item);
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      'https://img.example/original.png', 'https://img.example/large.png', 'https://img.example/medium.png',
    ]);
    expect(anchor.click).toHaveBeenCalledOnce();
  });

  it('uses the configured server proxy after CORS failure and retains the original source for filename fallback', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('CORS'))
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3])));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:proxy-image');
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { appendChild: vi.fn() } });
    vi.stubGlobal('window', { setTimeout: vi.fn() });
    const item = normalizeSearchResponse({ result: [{
      title: 'food', image_url: 'https://example.com/original.webp', thumbnail_url: 'https://example.com/large.png',
    }] }).items[0];
    await downloadImage(item, 'http://localhost:8320/');
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      'https://example.com/original.webp',
      'http://localhost:8320/api/images/download?url=https%3A%2F%2Fexample.com%2Foriginal.webp',
    ]);
    expect(anchor.download).toBe('food.webp');
    expect(anchor.click).toHaveBeenCalledOnce();
  });

  it('tries a distinct thumbnail after both original downloads fail, including same-origin proxy configuration', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('CORS'))
      .mockResolvedValueOnce(new Response(null, { status: 422 }))
      .mockResolvedValueOnce(new Response('image-data', { headers: { 'Content-Type': 'image/png' } }));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:proxy-image');
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { appendChild: vi.fn() } });
    vi.stubGlobal('window', { setTimeout: vi.fn() });
    const item = normalizeSearchResponse({ result: [{
      title: 'food', image_url: 'https://example.com/original.jpg', thumbnail_url: 'https://example.com/large.png',
    }] }).items[0];
    await downloadImage(item, '');
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      'https://example.com/original.jpg',
      '/api/images/download?url=https%3A%2F%2Fexample.com%2Foriginal.jpg',
      'https://example.com/large.png',
    ]);
    expect(anchor.download).toBe('food.png');
  });

  it('saves the actual image blob with a safe filename and releases the temporary URL', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('image-bytes', { headers: { 'Content-Type': 'image/png' } })));
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test-image');
    const revokeObjectUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    const appendChild = vi.fn();
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { appendChild } });
    const schedule = vi.fn();
    vi.stubGlobal('window', { setTimeout: schedule });
    const item = normalizeSearchResponse({ result: [{ title: '食材/西红柿', image_url: 'https://example.com/a.jpg' }] }).items[0];
    await downloadImage(item);
    expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob));
    expect(anchor.href).toBe('blob:test-image');
    expect(anchor.download).toBe('食材_西红柿.png');
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(anchor.remove).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).not.toHaveBeenCalled();
    schedule.mock.calls[0][0]();
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:test-image');
  });

  it('reports blocked cross-origin downloads without claiming success or opening a tab', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('CORS'));
    const open = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('open', open);
    const item = normalizeSearchResponse({ result: [{ image_url: 'https://example.com/a.png' }] }).items[0];
    await expect(downloadImage(item)).rejects.toThrow('打开原图');
    expect(fetchMock).toHaveBeenCalledWith('https://example.com/a.png', {
      mode: 'cors', credentials: 'omit', signal: expect.any(AbortSignal),
    });
    expect(open).not.toHaveBeenCalled();
  });

  it('does not save an HTML error page returned by an image source', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>denied</html>', { headers: { 'Content-Type': 'text/html' } })));
    const item = normalizeSearchResponse({ result: [{ image_url: 'https://example.com/a.png' }] }).items[0];
    await expect(downloadImage(item)).rejects.toThrow('图片下载失败');
  });

  it.each(['http', 'network', 'invalid-blob'])('retries a distinct thumbnail after an original %s failure and names the saved source', async failure => {
    const fetchMock = vi.fn();
    if (failure === 'http') fetchMock.mockResolvedValueOnce(new Response(null, { status: 403 }));
    else if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('CORS'));
    else fetchMock.mockResolvedValueOnce(new Response('<html>denied</html>', { headers: { 'Content-Type': 'text/html' } }));
    fetchMock.mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3])));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:thumbnail');
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { appendChild: vi.fn() } });
    vi.stubGlobal('window', { setTimeout: vi.fn() });
    const item = normalizeSearchResponse({ result: [{
      title: '食材', image_url: 'https://example.com/original.jpg', thumbnail_url: 'https://example.com/large.webp',
    }] }).items[0];
    await downloadImage(item);
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual(['https://example.com/original.jpg', 'https://example.com/large.webp']);
    expect(anchor.download).toBe('食材.webp');
    expect(anchor.click).toHaveBeenCalledOnce();
  });
});
