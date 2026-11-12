import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { acknowledgeNotice, noticePopup } from './notice'

// Fixtures only: every API and remote image request is answered below.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const SCENES = [{ id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] }]
/** One self-built hit (schemas/retrieval.py SearchHit). */
const HIT = {
  id: '0b6f2c1e-0000-4000-8000-000000000001', original_url: 'https://images.test/original-1.png', thumb_url: 'https://images.test/thumb-1.png',
  similarity: 0.9, category_id: 1, category: '番茄', scenes: ['general'], scene_label: null, label_status: 'verified', description: '', width: 640, height: 480,
}

/** sourceRefuses: the image host and the backend proxy refuse downloads (fetch); pictures (<img>) still show. */
async function mockBackend(page: Page, { sourceRefuses = false } = {}) {
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/categories') return route.fulfill({ json: { ok: true, categories: [], heating_groups: [], root_codes: ['dish'] } })
    if (path === '/api/search') return route.fulfill({ json: { ok: true, trace_id: 'e2e-overlay', source: 'self', scene: 'general', items: [HIT] } })
    return route.abort('blockedbyclient')
  })
  await page.route('https://images.test/**', route => (sourceRefuses && route.request().resourceType() === 'fetch'
    ? route.abort('blockedbyclient')
    : route.fulfill({ contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png })))
}

async function searchOnce(page: Page) {
  await page.goto('/')
  await page.getByLabel('选择查询图片').setInputFiles({ name: 'query.png', mimeType: 'image/png', buffer: png })
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  await expect(page.locator('.result-card')).toHaveCount(1)
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(`${name}.png`), animations: 'disabled' })
}

test('错误弹窗一直留到看过为止，给出补救的链接；Esc 关闭后焦点回到原处', async ({ page }, info) => {
  await mockBackend(page, { sourceRefuses: true })
  await searchOnce(page)
  const download = page.getByRole('button', { name: '下载图片：番茄' })
  await download.click()
  const popup = page.getByRole('alertdialog', { name: '图片下载失败' })
  await expect(popup).toBeVisible()
  await expect(popup.getByRole('link', { name: /打开原图/ })).toHaveAttribute('href', 'https://images.test/original-1.png')
  await expect(popup.getByRole('button', { name: '知道了' })).toBeFocused()
  await screenshot(page, info, 'notice-error')
  // An error does not close by itself (a success would have closed by now)
  await page.waitForTimeout(2600)
  await expect(popup).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(popup).toBeHidden()
  await expect(download).toBeFocused()
  // One control for every page: a single overlay element
  await expect(page.locator('dialog.status-overlay')).toHaveCount(1)
})

test('成功提示自己关闭；鼠标停在上面时先不关', async ({ page }, info) => {
  await mockBackend(page)
  await searchOnce(page)
  const download = page.getByRole('button', { name: '下载图片：番茄' })
  await download.click()
  const popup = noticePopup(page)
  await expect(popup).toContainText('已发起图片下载')
  await expect(popup).toHaveClass(/\bis-success\b/)
  await expect(page.getByRole('dialog', { name: '操作成功' })).toBeVisible()
  await screenshot(page, info, 'notice-success')
  await expect(popup).toHaveCount(0, { timeout: 4000 })  // closes on its own after about 2 s
  await expect(download).toBeFocused()

  await download.click()
  await expect(popup).toContainText('已发起图片下载')
  await popup.locator('.so-panel').hover()
  await page.waitForTimeout(2600)
  await expect(popup).toBeVisible()  // held while the pointer is on it
  await page.mouse.move(4, 4)
  await expect(popup).toHaveCount(0, { timeout: 4000 })
})

test('上传时不合格的图片逐条列在弹窗里，点空白处关闭', async ({ page }, info) => {
  await mockBackend(page)
  await page.goto('/')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^上传与导入/ }).click()
  await page.getByLabel('选择待入库图片').setInputFiles([
    { name: 'tomato.png', mimeType: 'image/png', buffer: png },
    { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') },
  ])
  const popup = page.getByRole('alertdialog', { name: '部分图片未添加' })
  await expect(popup).toContainText('已添加 1 张，另有 1 张没有添加')
  await expect(popup.getByRole('listitem')).toHaveText(['notes.txt：仅支持 JPG、PNG、WebP 图片'])
  await screenshot(page, info, 'notice-details')
  await page.mouse.click(8, 8)  // the backdrop, outside the panel
  await expect(popup).toBeHidden()
  await expect(page.locator('.mg-upload-card')).toHaveCount(1)
})

test('搜索页选错文件时用同一个弹窗提示', async ({ page }) => {
  await mockBackend(page)
  await page.goto('/')
  await page.getByLabel('选择查询图片').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') })
  await acknowledgeNotice(page, '请选择 JPG、PNG、WebP 或 BMP 格式的图片。', 'warn')
  await expect(page.getByRole('button', { name: '开始搜索', exact: true })).toBeDisabled()
})

test('窄屏：弹窗不超出屏幕、不产生横向滚动', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await mockBackend(page, { sourceRefuses: true })
  await searchOnce(page)
  await page.getByRole('button', { name: '下载图片：番茄' }).click()
  const box = (await page.getByRole('alertdialog', { name: '图片下载失败' }).boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
})
