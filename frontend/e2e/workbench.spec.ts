import { expect, test, type Page, type TestInfo } from '@playwright/test'

// Fixtures only: every API and remote image request is intercepted below.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const upload = (name = 'query.png') => ({ name, mimeType: 'image/png', buffer: png })
const rows = Array.from({ length: 15 }, (_, index) => ({
  title: `测试食材${String(index + 1).padStart(2, '0')}`,
  image_url: `https://images.test/${index === 0 ? 'broken' : `image-${index}`}.png`,
  similarity: index === 0 ? null : 0.4 + index * 0.035,
  category: index % 2 === 0 ? '蔬菜' : '水果',
  details: {
    site: { Name: '测试食材来源' },
    page_url: `https://source.test/food/${index + 1}`,
    text_snippets: [`测试来源说明 ${index + 1}`],
    images: [{ thumbnail_url: { large: `https://images.test/thumb-${index}.png` }, width: 640, height: 480 }],
  },
}))
const result = { result: rows, trace_id: 'e2e-controlled-fixture' }

async function mockSearch(page: Page, payload: unknown = result, status = 200) {
  await page.route('**/api/predict/**', route => route.fulfill({ status, json: payload }))
}

async function urlQuery(page: Page) {
  await page.getByRole('button', { name: '图片链接', exact: true }).click()
  await page.getByLabel('图片地址').fill('https://images.test/query.png')
}

async function search(page: Page) {
  await urlQuery(page)
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

async function navigate(page: Page, name: string) {
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
}

async function noHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport)
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport)
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', route => route.request().url().endsWith('/api/ping')
    ? route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    : route.abort('blockedbyclient'))
  await page.route('https://images.test/**', route => route.request().url().endsWith('/broken.png')
    ? route.fulfill({ status: 404, contentType: 'text/plain', body: 'Image not found' })
    : route.fulfill({
      contentType: 'image/png',
      headers: { 'access-control-allow-origin': '*' },
      body: png,
    }))
  await page.goto('/')
})

test('空页面显示真实连接状态及搜索引导', async ({ page }, info) => {
  await expect(page.getByRole('heading', { name: '等待查询图片' })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始搜索', exact: true })).toBeDisabled()
  await expect(page.getByText('搜索服务可连接', { exact: true })).toBeVisible()
  await expect(page.getByLabel('筛选类别')).toBeDisabled()
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'desktop-empty')
})

test('图片链接搜索显示结果、缺失分数与缩略图回退', async ({ page }, info) => {
  await mockSearch(page)
  const requestPromise = page.waitForRequest('**/api/predict/embedding_url')
  await search(page)
  const request = await requestPromise
  expect(request.method()).toBe('POST')
  expect(request.postDataJSON()).toEqual({ img_url: 'https://images.test/query.png' })
  await expect(page.locator('.result-card')).toHaveCount(12)
  await expect(page.getByText('共 15 张图片', { exact: true })).toBeVisible()
  await expect(page.locator('.result-card').first().getByText('未返回分数')).toBeVisible()
  await expect(page.locator('.result-card').nth(1).getByText('43.5% 相似')).toBeVisible()
  const fallback = page.locator('.result-card').first().getByRole('img', { name: '测试食材01', exact: true })
  await expect(fallback).toHaveAttribute('src', 'https://images.test/thumb-0.png')
  await expect.poll(() => fallback.evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(1)
  await screenshot(page, info, 'desktop-results')
})

test('查询文件以 multipart file 字段提交', async ({ page }) => {
  await mockSearch(page)
  await page.getByLabel('选择查询图片').setInputFiles(upload())
  await expect(page.getByRole('img', { name: '当前查询图片' })).toBeVisible()
  const requestPromise = page.waitForRequest('**/api/predict/embedding_file')
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  const request = await requestPromise
  expect(request.headers()['content-type']).toContain('multipart/form-data; boundary=')
  expect(request.postDataBuffer()?.toString()).toContain('name="file"; filename="query.png"')
  await expect(page.locator('.result-card')).toHaveCount(12)
})

test('结果支持分页、类别、关键词和相似度排序', async ({ page }) => {
  await mockSearch(page)
  await search(page)
  await expect(page.locator('.result-card')).toHaveCount(12)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.result-card')).toHaveCount(3)
  await expect(page.locator('.result-card').first()).toContainText('测试食材13')
  await page.getByLabel('筛选类别').selectOption('蔬菜')
  await expect(page.locator('.result-card')).toHaveCount(8)
  await expect(page.getByText('共 15 张图片 · 筛选后 8 张', { exact: true })).toBeVisible()
  await page.getByLabel('筛选类别').selectOption('all')
  await page.getByLabel('结果排序').selectOption('similarity')
  await expect(page.locator('.result-card').first()).toContainText('测试食材15')
  await page.getByLabel('搜索结果标题或来源').fill('测试食材02')
  await expect(page.locator('.result-card')).toHaveCount(1)
  await expect(page.locator('.result-card').first()).toContainText('测试食材02')
  await page.getByLabel('搜索结果标题或来源').fill('没有这个标题')
  await expect(page.getByRole('heading', { name: '没有符合筛选条件的结果' })).toBeVisible()
  await page.getByRole('button', { name: '清除筛选' }).click()
  await expect(page.locator('.result-card')).toHaveCount(12)
})

test('查看详情并从缩略图下载失败原图的图片', async ({ page }, info) => {
  await mockSearch(page)
  await search(page)
  await page.getByRole('button', { name: '查看图片详情：测试食材01', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('640 × 480 px')).toBeVisible()
  await expect(dialog.getByText('测试来源说明 1', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('link', { name: '来源网页' })).toHaveAttribute('href', 'https://source.test/food/1')
  await screenshot(page, info, 'desktop-detail')
  const downloadPromise = page.waitForEvent('download')
  await dialog.getByRole('button', { name: '下载图片', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('测试食材01.png')
  expect(await download.failure()).toBeNull()
  await dialog.getByRole('button', { name: '关闭弹窗' }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText('已发起图片下载，请在浏览器下载列表中查看。')).toBeVisible()
})

test('网格与列表切换保留筛选、分页和详情入口', async ({ page }, info) => {
  await mockSearch(page)
  await search(page)
  const listToggle = page.getByRole('button', { name: '列表展示结果', exact: true })
  const gridToggle = page.getByRole('button', { name: '网格展示结果', exact: true })
  await listToggle.click()
  await expect(listToggle).toHaveAttribute('aria-pressed', 'true')
  await expect(gridToggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.locator('.result-table tbody tr')).toHaveCount(12)
  await expect(page.locator('.result-table tbody tr').first()).toContainText('测试食材01')
  await screenshot(page, info, 'desktop-list')
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.result-table tbody tr')).toHaveCount(3)
  await expect(page.locator('.result-table tbody tr').first()).toContainText('测试食材13')
  await gridToggle.click()
  await expect(page.locator('.result-card')).toHaveCount(3)
  await expect(page.locator('.result-card').first()).toContainText('测试食材13')
  await page.getByLabel('筛选类别').selectOption('水果')
  await expect(page.locator('.result-card')).toHaveCount(7)
  await listToggle.click()
  await expect(page.locator('.result-table tbody tr')).toHaveCount(7)
  await page.getByRole('button', { name: '查看图片详情：测试食材02', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '图片详情' })).toBeVisible()
  await noHorizontalOverflow(page)
})

test('侧栏收起展开后导航与查询结果保持可用', async ({ page }, info) => {
  await mockSearch(page)
  await search(page)
  const sidebar = page.locator('.sidebar')
  const expandedWidth = await sidebar.evaluate(element => element.getBoundingClientRect().width)
  const collapse = page.getByRole('button', { name: '收起侧栏', exact: true })
  await expect(collapse).toHaveAttribute('aria-expanded', 'true')
  await collapse.click()
  const expand = page.getByRole('button', { name: '展开侧栏', exact: true })
  await expect(expand).toHaveAttribute('aria-expanded', 'false')
  await expect.poll(() => sidebar.evaluate(element => element.getBoundingClientRect().width)).toBeLessThan(expandedWidth / 2)
  await expect(page.getByRole('heading', { name: '图搜图', exact: true })).toBeVisible()
  await expect(page.locator('.result-card')).toHaveCount(12)
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'desktop-collapsed')
  await navigate(page, '图片数据')
  await expect(page.getByRole('heading', { name: '图片数据管理', exact: true })).toBeVisible()
  await navigate(page, '图搜图')
  await expect(page.locator('.result-card')).toHaveCount(12)
  await expand.click()
  await expect(page.getByRole('button', { name: '收起侧栏', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect.poll(() => sidebar.evaluate(element => element.getBoundingClientRect().width)).toBe(expandedWidth)
})

test('桌面结果区域独立滚动且工具栏分页保持在视口内', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockSearch(page)
  await search(page)
  const queryBounds = await page.locator('.query-panel').boundingBox()
  const resultBounds = await page.locator('.results-panel').boundingBox()
  expect(queryBounds).not.toBeNull()
  expect(resultBounds).not.toBeNull()
  expect(queryBounds!.x + queryBounds!.width).toBeLessThanOrEqual(resultBounds!.x)
  expect(Math.abs(queryBounds!.y - resultBounds!.y)).toBeLessThanOrEqual(1)
  for (const control of [page.getByRole('button', { name: '开始搜索', exact: true }), page.getByLabel('搜索结果标题或来源')]) {
    expect(await control.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(14)
  }
  const content = page.locator('.results-content')
  const toolbar = page.locator('.result-toolbar')
  const footer = page.locator('.results-footer')
  const topbar = page.locator('.topbar')
  await expect(page.locator('.result-card')).toHaveCount(12)
  await expect(toolbar).toBeInViewport()
  await expect(footer).toBeInViewport()
  const before = { toolbar: await toolbar.boundingBox(), footer: await footer.boundingBox(), topbar: await topbar.boundingBox() }
  await content.hover()
  await page.mouse.wheel(0, 2000)
  await expect.poll(() => content.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await expect(toolbar).toBeInViewport()
  await expect(footer).toBeInViewport()
  expect(await toolbar.boundingBox()).toEqual(before.toolbar)
  expect(await footer.boundingBox()).toEqual(before.footer)
  expect(await topbar.boundingBox()).toEqual(before.topbar)
  expect(await page.evaluate(() => ({ window: window.scrollY, document: document.documentElement.scrollTop, body: document.body.scrollTop }))).toEqual({ window: 0, document: 0, body: 0 })
  await screenshot(page, info, 'desktop-scrolled-results')
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.result-card')).toHaveCount(3)
})

test('矮屏详情正文独立滚动且关闭下载按钮始终可见', async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 600 })
  await mockSearch(page, { result: [{ ...rows[0], details: { ...rows[0]!.details, text_snippets: Array.from({ length: 24 }, (_, index) => `详情正文说明 ${index + 1}`) } }] })
  await search(page)
  await page.getByRole('button', { name: '查看图片详情：测试食材01', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  const body = dialog.locator('.dialog-body')
  const close = dialog.getByRole('button', { name: '关闭弹窗', exact: true })
  const download = dialog.getByRole('button', { name: '下载图片', exact: true })
  await expect(close).toBeInViewport()
  await expect(download).toBeInViewport()
  const before = { close: await close.boundingBox(), download: await download.boundingBox() }
  await body.hover()
  await page.mouse.wheel(0, 2000)
  await expect.poll(() => body.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await expect(close).toBeInViewport()
  await expect(download).toBeInViewport()
  expect(await close.boundingBox()).toEqual(before.close)
  expect(await download.boundingBox()).toEqual(before.download)
  await screenshot(page, info, 'desktop-short-detail')
  await close.click()
  await expect(dialog).not.toBeVisible()
})

test('下载被源站拒绝时提供手动保存链接', async ({ page }) => {
  await mockSearch(page)
  await search(page)
  await expect(page.locator('.result-card')).toHaveCount(12)
  await page.route('https://images.test/**', route => route.abort('blockedbyclient'))
  await page.getByRole('button', { name: '下载图片：测试食材01', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('图片下载失败')
  await expect(page.getByRole('link', { name: '打开原图', exact: true })).toHaveAttribute('href', 'https://images.test/broken.png')
})

test('上游空结果和 HTTP 422 错误准确反馈', async ({ page }) => {
  await mockSearch(page, { result: { results: [], status: 'upstream_empty_response', upstream_error: { code: 500, message: 'empty' } }, trace_id: 'empty-fixture' })
  await search(page)
  await expect(page.getByRole('heading', { name: '暂未找到相关图片' })).toBeVisible()
  await expect(page.getByText('上游搜索服务暂未返回结果，请稍后重试或更换图片。')).toBeVisible()
  await expect(page.getByLabel('筛选类别')).toBeDisabled()
  await mockSearch(page, { detail: [{ loc: ['body', 'img_url'], msg: 'Invalid image URL', type: 'value_error' }] }, 422)
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('HTTP 422')
  await expect(page.getByRole('alert')).toContainText('body.img_url: Invalid image URL')
  await expect(page.locator('.result-card')).toHaveCount(0)
})

test('未提供类别时筛选禁用且不从标题推断', async ({ page }) => {
  await mockSearch(page, { result: [{ title: '蔬菜西红柿', image_url: 'https://images.test/no-category.png', similarity: null }] })
  await search(page)
  await expect(page.locator('.result-card')).toHaveCount(1)
  await expect(page.getByLabel('筛选类别')).toBeDisabled()
  await expect(page.getByText('· 暂无类别数据')).toBeVisible()
  await expect(page.getByText('未返回分数', { exact: true })).toBeVisible()
})

test('取消正在等待的请求并忽略迟到结果', async ({ page }) => {
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/predict/**', async route => {
    await gate
    await route.fulfill({ json: result }).catch(() => {})
  })
  try {
    const requestPromise = page.waitForRequest('**/api/predict/embedding_url')
    await search(page)
    await requestPromise
    await expect(page.getByRole('button', { name: '取消搜索', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '取消搜索', exact: true }).click()
    await expect(page.getByText('已取消本次搜索。')).toBeVisible()
    release()
    await expect(page.getByRole('button', { name: '开始搜索', exact: true })).toBeEnabled()
    await expect(page.locator('.result-card')).toHaveCount(0)
  } finally { release() }
})

test('图片导入队列支持预览移除且不提交后端', async ({ page }, info) => {
  const writes: string[] = []
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()) })
  await navigate(page, '上传与导入')
  await page.getByLabel('选择待导入图片').setInputFiles([upload('tomato.png'), upload('carrot.png')])
  await expect(page.locator('.mg-upload-card')).toHaveCount(2)
  await expect(page.getByRole('button', { name: '提交入库 · 待接入', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '预览 tomato.png', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '图片预览' }).getByRole('img', { name: 'tomato.png', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭弹窗' }).click()
  await page.getByRole('button', { name: '移除 carrot.png', exact: true }).click()
  await expect(page.locator('.mg-upload-card')).toHaveCount(1)
  await screenshot(page, info, 'desktop-import')
  await page.getByRole('button', { name: '清空列表', exact: true }).click()
  await expect(page.locator('.mg-upload-card')).toHaveCount(0)
  expect(writes).toEqual([])
})

test('食材本地草稿可新增编辑筛选删除', async ({ page }, info) => {
  const writes: string[] = []
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()) })
  await navigate(page, '食材管理')
  await page.getByRole('button', { name: '新建食材', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '新建食材草稿' })
  await editor.getByLabel(/食材名称/).fill('西红柿')
  await editor.getByLabel(/食材类别/).selectOption('水果')
  await editor.getByLabel(/食材属性/).fill('红色、圆形')
  await editor.getByRole('button', { name: '保存本地草稿' }).click()
  await expect(page.getByRole('button', { name: '编辑 西红柿', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '编辑 西红柿', exact: true }).click()
  const editing = page.getByRole('dialog', { name: '编辑食材草稿' })
  await editing.getByLabel(/食材名称/).fill('番茄')
  await editing.getByLabel(/食材类别/).selectOption('蔬菜')
  await editing.getByRole('button', { name: '保存本地草稿' }).click()
  await expect(page.getByRole('button', { name: '编辑 番茄', exact: true })).toBeVisible()
  await page.getByLabel('食材类别筛选').selectOption('水果')
  await expect(page.getByRole('heading', { name: '没有匹配的食材草稿' })).toBeVisible()
  await page.getByRole('button', { name: '重置筛选' }).click()
  await screenshot(page, info, 'desktop-ingredients')
  await page.getByRole('button', { name: '删除 番茄', exact: true }).click()
  await expect(page.getByRole('heading', { name: '暂无食材草稿' })).toBeVisible()
  expect(writes).toEqual([])
})

test('390px 窄屏搜索与管理页面不横向溢出', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const queryBounds = await page.locator('.query-panel').boundingBox()
  const resultBounds = await page.locator('.results-panel').boundingBox()
  expect(queryBounds).not.toBeNull()
  expect(resultBounds).not.toBeNull()
  expect(queryBounds!.y + queryBounds!.height).toBeLessThanOrEqual(resultBounds!.y)
  expect(await page.getByRole('button', { name: '开始搜索', exact: true }).evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(14)
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'mobile-empty')
  await mockSearch(page)
  await search(page)
  await expect(page.locator('.result-card')).toHaveCount(12)
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'mobile-results')
  await page.getByRole('button', { name: '查看图片详情：测试食材01', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '图片详情' })).toBeVisible()
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'mobile-detail')
  await page.getByRole('button', { name: '关闭弹窗' }).click()
  await page.getByRole('button', { name: '列表展示结果', exact: true }).click()
  await expect(page.locator('.result-table tbody tr')).toHaveCount(12)
  await noHorizontalOverflow(page)
  await screenshot(page, info, 'mobile-list')
  for (const name of ['图片数据', '上传与导入', '入库任务', '食材管理']) {
    await navigate(page, name)
    await noHorizontalOverflow(page)
  }
  await screenshot(page, info, 'mobile-management')
})
