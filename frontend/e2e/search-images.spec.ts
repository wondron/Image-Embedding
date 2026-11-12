import { expect, test, type Page } from '@playwright/test'

// Fixtures only: every API and image request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const SCENES = [{ id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] }]

/** A self-built hit (§6.1): original and 512 px thumbnail under `host`. */
const hit = (n: number, host = 'https://cdn.test') => ({
  id: imageId(n), original_url: `${host}/original/${n}.jpg`, thumb_url: `${host}/thumb/${n}.webp`,
  similarity: 0.9 - n * 0.01, category_id: 1, category: `食材${n}`, scenes: ['general'], scene_label: null,
  label_status: 'verified', description: '', width: 1600, height: 1200,
})

/**
 * requested: paths asked of https://cdn.test (404 when the path says "missing"); unreachable: of the
 * mock storage's placeholder host; files: the API's copies. holdOriginals keeps every original
 * pending until releaseOriginals().
 */
async function mockApi(page: Page, { hits = [] as unknown[], predict = null as unknown, holdOriginals = false } = {}) {
  const requested: string[] = []
  const unreachable: string[] = []
  const files: string[] = []
  let release = () => {}
  const released = new Promise<void>(resolve => { release = resolve })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
    if (path === '/api/search') {
      return route.fulfill({ json: { ok: true, trace_id: 't', source: 'self', scene: 'general', outputs_heating: false, items: hits } })
    }
    if (path.startsWith('/api/predict/')) return route.fulfill({ json: predict })
    const file = /^\/api\/images\/([^/]+)\/file$/.exec(path)
    if (file) {
      files.push(`${file[1]}:${url.searchParams.get('variant')}`)
      return route.fulfill({ contentType: 'image/png', body: png })
    }
    return route.abort('blockedbyclient')
  })
  await page.route('https://cdn.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    requested.push(path)
    if (holdOriginals && path.startsWith('/original/')) await released
    if (path.includes('missing')) return route.fulfill({ status: 404, body: 'gone' })
    return route.fulfill({ contentType: 'image/png', body: png })
  })
  await page.route('https://cdn.example.local/**', route => {
    unreachable.push(new URL(route.request().url()).pathname)
    return route.abort('namenotresolved')
  })
  return { requested, unreachable, files, releaseOriginals: () => release() }
}

async function searchByUrl(page: Page, target: '自建库' | '现网 Global' = '自建库') {
  await page.goto('/')
  if (target !== '自建库') await page.getByRole('button', { name: target, exact: true }).click()
  await page.getByRole('button', { name: '图片链接', exact: true }).click()
  await page.getByLabel('图片地址').fill('https://cdn.test/query.png')
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
}

const naturalWidth = (page: Page, selector: string) => page.locator(selector).evaluate(img => (img as HTMLImageElement).naturalWidth)

test('自建库结果：列表只加载 512px 缩略图；点开详情先显示缩略图，原图加载完再换上', async ({ page }) => {
  const mock = await mockApi(page, { hits: Array.from({ length: 14 }, (_, i) => hit(i + 1)), holdOriginals: true })
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(12)
  const first = cards.first().getByRole('img', { name: '食材1', exact: true })
  await expect(first).toHaveAttribute('src', 'https://cdn.test/thumb/1.webp')
  await expect.poll(() => first.evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1)
  const images = () => mock.requested.filter(path => path !== '/query.png')
  expect(images().length).toBeGreaterThan(0)
  expect(images().filter(path => !path.startsWith('/thumb/'))).toEqual([])  // no original yet

  await cards.first().getByRole('button', { name: '查看图片详情：食材1' }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  await expect(dialog.locator('.progressive-image-preview')).toHaveAttribute('src', 'https://cdn.test/thumb/1.webp')
  await expect(dialog.getByText('正在加载原图')).toBeVisible()
  await expect.poll(images).toContain('/original/1.jpg')

  mock.releaseOriginals()
  await expect(dialog.locator('.progressive-image-full.is-loaded')).toHaveAttribute('src', 'https://cdn.test/original/1.jpg')
  await expect(dialog.getByText('正在加载原图')).toHaveCount(0)
  await expect(dialog.locator('.progressive-image-preview')).toHaveCount(0)
  expect(images().filter(path => path.startsWith('/original/'))).toEqual(['/original/1.jpg'])  // only the one opened
})

test('现网结果：列表用缩略图（大图失败退到中图），原图只在详情里加载；原图打不开时保留缩略图并说明', async ({ page }) => {
  const mock = await mockApi(page, {
    predict: {
      trace_id: 't',
      result: [{
        title: '外部图片', image_url: 'https://cdn.test/original/missing.jpg',
        thumbnail_url: { large: 'https://cdn.test/thumb/missing-large.png', medium: 'https://cdn.test/thumb/medium.png', small: 'https://cdn.test/thumb/small.png' },
      }],
    },
  })
  await searchByUrl(page, '现网 Global')
  const image = page.locator('.result-card').getByRole('img', { name: '外部图片', exact: true })
  await expect(image).toHaveAttribute('src', 'https://cdn.test/thumb/medium.png')
  await expect.poll(() => image.evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1)  // its request is recorded by now
  expect(mock.requested.filter(path => path !== '/query.png')).toEqual(['/thumb/missing-large.png', '/thumb/medium.png'])

  await page.locator('.result-card').getByRole('button', { name: '查看图片详情：外部图片' }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  await expect(dialog.getByText('原图加载失败，显示的是缩略图')).toBeVisible()
  await expect(dialog.locator('.progressive-image-preview')).toHaveAttribute('src', 'https://cdn.test/thumb/medium.png')
  expect(mock.requested).toContain('/original/missing.jpg')
})

test('自建库图片的存储地址打不开：列表用后端读出的缩略图，详情直接从后端读原图', async ({ page }) => {
  const mock = await mockApi(page, { hits: [hit(1, 'https://cdn.example.local/image-embedding')] })
  await searchByUrl(page)
  const thumb = page.locator('.result-card').getByRole('img', { name: '食材1', exact: true })
  await expect(thumb).toHaveAttribute('src', `/api/images/${imageId(1)}/file?variant=thumb`)
  await expect.poll(() => naturalWidth(page, '.result-card img')).toBe(1)

  await page.locator('.result-card').getByRole('button', { name: '查看图片详情：食材1' }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  await expect(dialog.locator('.progressive-image-full.is-loaded')).toHaveAttribute('src', `/api/images/${imageId(1)}/file?variant=original`)
  // The placeholder host failed once for the thumbnail; the original went to the API straight away
  expect(mock.unreachable).toEqual(['/image-embedding/thumb/1.webp'])
  expect(mock.files.filter(file => file.endsWith(':original'))).toEqual([`${imageId(1)}:original`])
})
