import { expect, test, type Page } from '@playwright/test'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const CATEGORIES = [{
  id: 1, code: 'dish.steak', parent_id: null, path: '1', name: '牛排', names: [{ lang: 'zh', name: '牛排', kind: 'primary' }], cuisines: [],
  heating_group: null, sort_order: 0, is_active: true, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
}]
const SCENES = [{ id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] }]

/**
 * Images stored by the mock storage: their URLs name a placeholder host the browser cannot reach
 * (cdn.example.local), but the API can read the files back (GET /api/images/{id}/file).
 */
async function mockApi(page: Page, count = 3, { holdOriginals = false } = {}) {
  const calls = { direct: [] as string[], files: [] as string[], releaseOriginals: () => {} }
  const released = new Promise<void>(resolve => { calls.releaseOriginals = resolve })
  const item = (n: number) => ({
    id: imageId(n), original_url: `https://cdn.example.local/image-embedding/original/${n}.png`,
    thumb_url: `https://cdn.example.local/image-embedding/thumb/${n}.webp`, category_id: 1, category: '牛排', scenes: ['general'],
    label_status: 'auto', description: `图片${n}`, tags: [], width: 640, height: 480, created_at: '2026-11-10T08:00:00+00:00', updated_at: null,
    vector_status: 'synced',
  })
  await page.route('https://cdn.example.local/**', route => {
    calls.direct.push(route.request().url())
    return route.abort('namenotresolved')
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/categories') return route.fulfill({ json: { ok: true, categories: CATEGORIES, heating_groups: [], root_codes: ['dish'] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
    if (path === '/api/images') {
      const size = Number(url.searchParams.get('page_size'))
      return route.fulfill({ json: { ok: true, trace_id: 't', scene: 'general', page: 1, page_size: size, total: count, items: Array.from({ length: count }, (_, i) => item(i + 1)) } })
    }
    const file = /^\/api\/images\/([^/]+)\/file$/.exec(path)
    if (file) {
      calls.files.push(`${file[1]}:${url.searchParams.get('variant')}`)
      if (holdOriginals && url.searchParams.get('variant') === 'original') await released
      return route.fulfill({ contentType: 'image/png', body: png, headers: { 'Cache-Control': 'no-store' } })
    }
    return route.abort('blockedbyclient')
  })
  return calls
}

async function openLibrary(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^图片数据/ }).click()
}

const loaded = (page: Page) => page.locator('.mg-library-card img').evaluateAll(images =>
  images.map(img => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0))

test('存储地址打不开的缩略图改由后端读出；之后同一地址直接走后端', async ({ page }) => {
  const calls = await mockApi(page)
  await openLibrary(page)
  const images = page.locator('.mg-library-card img')
  await expect(images).toHaveCount(3)
  await expect.poll(() => loaded(page)).toEqual([true, true, true])
  for (const [n, img] of (await images.all()).entries()) {
    await expect(img).toHaveAttribute('src', `/api/images/${imageId(n + 1)}/file?variant=thumb`)
  }
  expect(calls.files.sort()).toEqual([1, 2, 3].map(n => `${imageId(n)}:thumb`))
  const tried = calls.direct.length
  expect(tried).toBeGreaterThan(0)

  // Shown again (another layout), the images go to the API at once: the placeholder host is not tried again
  await page.getByRole('button', { name: '列表展示' }).click()
  await expect.poll(() => page.locator('.mg-library-row img').evaluateAll(images =>
    images.map(img => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0))).toEqual([true, true, true])
  expect(calls.direct.length).toBe(tried)
})

test('图片编辑：先显示缩略图，原图加载完再换上；存储地址打不开时两者都从后端读', async ({ page }) => {
  const calls = await mockApi(page, 1, { holdOriginals: true })
  await page.route(url => /\/api\/images\/[^/]+$/.test(url.pathname), route => route.fulfill({ status: 503, json: { ok: false, error: 'NOT_READY', detail: 'down', trace_id: 't' } }))
  await openLibrary(page)
  await page.getByRole('button', { name: /^标注 / }).first().click()
  const figure = page.locator('.ie-figure')
  await expect(figure.locator('.progressive-image-preview')).toHaveAttribute('src', `/api/images/${imageId(1)}/file?variant=thumb`)
  await expect(figure.getByText('正在加载原图')).toBeVisible()
  calls.releaseOriginals()
  await expect(figure.locator('.progressive-image-full.is-loaded')).toHaveAttribute('src', `/api/images/${imageId(1)}/file?variant=original`)
  await expect(figure.locator('img')).toHaveCount(1)
  expect(calls.files.filter(file => file.endsWith(':original'))).toEqual([`${imageId(1)}:original`])
})
