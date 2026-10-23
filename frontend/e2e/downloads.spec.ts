import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { strFromU8, unzipSync } from 'fflate'

// All remote requests and the directory picker are controlled fixtures.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const upload = (name: string) => ({ name, mimeType: 'image/png', buffer: png })
const rows = [
  { title: '番茄:红', image_url: 'https://download-fixture.test/tomato.png', similarity: 0.91 },
  { title: '黄瓜', image_url: 'https://download-fixture.test/cucumber.png', similarity: 0.82 },
  { title: '失败图片', image_url: 'https://download-fixture.test/failed.png', similarity: 0.73 },
]

async function searchAndSelect(page: Page, selectedRows = rows) {
  await page.route('**/api/predict/embedding_file', route => route.fulfill({ json: { result: selectedRows } }))
  await page.getByLabel('选择查询图片', { exact: true }).setInputFiles(upload('query.png'))
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  await expect(page.locator('.result-card')).toHaveCount(selectedRows.length)
  await page.getByRole('button', { name: '选择全部结果', exact: true }).click()
  await page.getByRole('button', { name: /^批量下载（/ }).click()
  return page.getByRole('dialog', { name: '下载所选图片', exact: true })
}

async function archiveEntries(page: Page, info: TestInfo, action: () => Promise<unknown>, name: string) {
  const waiting = page.waitForEvent('download')
  await action()
  const download = await waiting
  expect(download.suggestedFilename()).toMatch(/^images-.*\.zip$/)
  const path = info.outputPath(name)
  await download.saveAs(path)
  expect(await download.failure()).toBeNull()
  return unzipSync(new Uint8Array(await readFile(path)))
}

function gate() {
  let release: () => void = () => {}
  const ready = new Promise<void>(resolve => { release = resolve })
  return { ready, release }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('food-atlas-api', ''))
  await page.route('**/api/**', route => route.request().url().endsWith('/api/ping')
    ? route.fulfill({ json: { ok: true } })
    : route.fulfill({ status: 404, json: { detail: 'Controlled endpoint not available' } }))
  await page.route('https://download-fixture.test/**', route => route.fulfill({
    contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png,
  }))
  await page.goto('/')
})

test('ZIP 包保留真实图片、来源清单与失败项，并可只重试失败图片', async ({ page }, info) => {
  let failedAvailable = false
  await page.route('https://download-fixture.test/failed.png', route => {
    if (route.request().resourceType() === 'image' || failedAvailable) {
      return route.fulfill({ contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png })
    }
    return route.fulfill({ status: 404, headers: { 'access-control-allow-origin': '*' }, body: 'Controlled missing image' })
  })
  const dialog = await searchAndSelect(page)
  const files = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '开始下载', exact: true }).click(), 'partial-results.zip')
  expect(Object.keys(files).sort()).toEqual(['0001-番茄_红.png', '0002-黄瓜.png', 'results.json'].sort())
  expect(Buffer.from(files['0001-番茄_红.png'])).toEqual(png)
  expect(Buffer.from(files['0002-黄瓜.png'])).toEqual(png)
  const manifest = JSON.parse(strFromU8(files['results.json']))
  expect(manifest.results).toHaveLength(2)
  expect(manifest.results[0]).toMatchObject({ title: '番茄:红', source: rows[0].image_url, similarity: 0.91, bytes: png.length })
  expect(manifest.failures).toEqual([expect.objectContaining({ title: '失败图片', imageUrl: rows[2].image_url, reason: 'HTTP 404' })])
  await expect(dialog.getByRole('heading', { name: '失败记录 · 1', exact: true })).toBeVisible()
  await expect(dialog.getByText('已将含 2 张图片及来源清单的 ZIP 交给浏览器，请查看下载列表确认保存结果。', { exact: true })).toBeVisible()
  await page.screenshot({ path: info.outputPath('desktop-download-partial.png'), fullPage: true })
  failedAvailable = true
  const retried = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '重试失败项（1）', exact: true }).click(), 'retried-failure.zip')
  expect(Object.keys(retried).sort()).toEqual(['0001-失败图片.png', 'results.json'].sort())
  const retryManifest = JSON.parse(strFromU8(retried['results.json']))
  expect(retryManifest.results).toEqual([expect.objectContaining({ id: 'image-3', title: '失败图片' })])
  expect(retryManifest.failures).toEqual([])
  await expect(dialog.getByRole('heading', { name: '失败记录 · 1', exact: true })).toHaveCount(0)
})

test('来源拒绝跨域下载时通过后端下载接口获取图片', async ({ page }, info) => {
  const proxyUrls: string[] = []
  await page.route('https://download-fixture.test/tomato.png', route => route.request().resourceType() === 'image'
    ? route.fulfill({ contentType: 'image/png', body: png })
    : route.abort('accessdenied'))
  await page.route('**/api/images/download?*', route => {
    proxyUrls.push(new URL(route.request().url()).searchParams.get('url') ?? '')
    return route.fulfill({ contentType: 'image/png', body: png })
  })
  const dialog = await searchAndSelect(page, [rows[0]])
  const files = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '开始下载', exact: true }).click(), 'proxy-result.zip')
  expect(proxyUrls).toEqual([rows[0].image_url])
  expect(Buffer.from(files['0001-番茄_红.png'])).toEqual(png)
  const manifest = JSON.parse(strFromU8(files['results.json']))
  expect(manifest.results[0].source).toBe(rows[0].image_url)
  expect(manifest.failures).toEqual([])
})

test('选择目录只写入用户选择的新子目录，并保存来源清单', async ({ page }, info) => {
  await page.addInitScript(() => {
    const capture = { picked: 0, directories: [] as string[], files: {} as Record<string, number[]> }
    Object.assign(window, { __testDirectory: capture })
    function directory(name: string, prefix = name): unknown {
      const children = new Map<string, unknown>()
      return {
        name,
        async getDirectoryHandle(child: string, options?: { create?: boolean }) {
          if (children.has(child)) return children.get(child)
          if (!options?.create) throw new DOMException('Controlled missing directory', 'NotFoundError')
          const next = directory(child, `${prefix}/${child}`)
          children.set(child, next)
          capture.directories.push(`${prefix}/${child}`)
          return next
        },
        async getFileHandle(filename: string) {
          return { async createWritable() {
            let pending: number[] = []
            return {
              async write(bytes: Uint8Array) { pending = Array.from(bytes) },
              async close() { capture.files[`${prefix}/${filename}`] = pending },
              async abort() { pending = [] },
            }
          } }
        },
      }
    }
    Object.defineProperty(window, 'showDirectoryPicker', { configurable: true,
      value: async () => { capture.picked++; return directory('测试下载目录') } })
  })
  await page.reload()
  const dialog = await searchAndSelect(page, rows.slice(0, 2))
  await dialog.getByRole('radio', { name: /保存到指定目录/ }).check()
  await expect(dialog.getByRole('button', { name: '开始下载', exact: true })).toBeDisabled()
  await dialog.getByRole('button', { name: '选择目录', exact: true }).click()
  await expect(dialog.getByText('已选择：测试下载目录', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '开始下载', exact: true }).click()
  await expect(dialog.getByText('已保存 2 张图片及 results.json 来源清单。', { exact: true })).toBeVisible()
  const saved = await page.evaluate(() => (window as unknown as { __testDirectory: { picked: number; directories: string[]; files: Record<string, number[]> } }).__testDirectory)
  expect(saved.picked).toBe(1)
  expect(saved.directories).toHaveLength(1)
  expect(saved.directories[0]).toMatch(/^测试下载目录\/images-/)
  expect(Object.keys(saved.files).sort()).toEqual([
    `${saved.directories[0]}/0001-番茄_红.png`, `${saved.directories[0]}/0002-黄瓜.png`, `${saved.directories[0]}/results.json`,
  ].sort())
  expect(Buffer.from(saved.files[`${saved.directories[0]}/0001-番茄_红.png`])).toEqual(png)
  expect(JSON.parse(Buffer.from(saved.files[`${saved.directories[0]}/results.json`]).toString('utf8')).results).toHaveLength(2)
  await expect(dialog.locator('.download-report-destination')).toContainText('测试下载目录 / images-')
  await page.screenshot({ path: info.outputPath('desktop-download-directory.png'), fullPage: true })
})

test('停止 ZIP 下载后不生成文件，晚到图片响应不会触发保存', async ({ page }) => {
  const blocked = gate()
  const fulfilled = gate()
  const downloads: string[] = []
  let fetching = false
  page.on('download', download => downloads.push(download.suggestedFilename()))
  await page.route('https://download-fixture.test/tomato.png', async route => {
    if (route.request().resourceType() === 'image') {
      await route.fulfill({ contentType: 'image/png', body: png }); return
    }
    fetching = true
    await blocked.ready
    await route.fulfill({ contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png }).catch(() => {})
    fulfilled.release()
  })
  try {
    const dialog = await searchAndSelect(page, rows.slice(0, 2))
    await dialog.getByRole('button', { name: '开始下载', exact: true }).click()
    await expect.poll(() => fetching).toBe(true)
    await dialog.getByRole('button', { name: '停止下载', exact: true }).click()
    await expect(dialog.getByText('已停止，本次未生成 ZIP，也未保存图片。', { exact: true })).toBeVisible()
    blocked.release()
    await fulfilled.ready
    await expect(dialog.getByRole('button', { name: '重新下载全部', exact: true })).toBeEnabled()
    expect(downloads).toEqual([])
    await expect(dialog.locator('.download-failures')).toHaveCount(0)
  } finally { blocked.release() }
})

test('批量查询停止后可继续，晚到的旧请求不会覆盖恢复后的结果', async ({ page }, info) => {
  const lateResponse = gate()
  const lateFinished = gate()
  const filenames: string[] = []
  await page.route('**/api/predict/embedding_file', async route => {
    const filename = /filename="([^"]+)"/.exec(route.request().postDataBuffer()?.toString() ?? '')?.[1] ?? ''
    filenames.push(filename)
    const isOldRequest = filenames.length === 1
    if (isOldRequest) await lateResponse.ready
    await route.fulfill({ json: { result: [{ ...rows[0], title: isOldRequest ? '过期结果' : filename === 'first.png' ? '恢复后的结果' : '第二张结果' }] } }).catch(() => {})
    if (isOldRequest) lateFinished.release()
  })
  try {
    await page.getByRole('button', { name: '批量搜索', exact: true }).click()
    await page.getByLabel('选择批量查询图片').setInputFiles([upload('first.png'), upload('second.png')])
    await page.getByRole('button', { name: '开始批量搜索', exact: true }).click()
    await expect.poll(() => filenames).toEqual(['first.png'])
    await page.getByRole('button', { name: '停止批量搜索', exact: true }).click()
    await expect(page.getByText('已停止，可继续', { exact: true })).toBeVisible()
    await expect(page.getByText('等待搜索', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '继续搜索', exact: true }).click()
    await expect(page.getByRole('button', { name: '查看结果：second.png', exact: true })).toBeEnabled()
    await expect(page.locator('.result-card')).toContainText('恢复后的结果')
    lateResponse.release()
    await lateFinished.ready
    expect(filenames).toEqual(['first.png', 'first.png', 'second.png'])
    await expect(page.locator('.result-card')).toContainText('恢复后的结果')
    await expect(page.getByText('过期结果', { exact: true })).toHaveCount(0)
    await expect(page.locator('.batch-job-error')).toHaveCount(0)
    await page.getByRole('button', { name: '查看结果：second.png', exact: true }).click()
    await expect(page.locator('.result-card')).toContainText('第二张结果')
    await page.screenshot({ path: info.outputPath('desktop-resumed-batch.png'), fullPage: true })
  } finally { lateResponse.release() }
})

test('390px 下载弹窗与批量队列可见且不横向溢出', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const dialog = await searchAndSelect(page, rows.slice(0, 2))
  await expect(dialog.getByRole('button', { name: '开始下载', exact: true })).toBeVisible()
  const bounds = await dialog.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.width).toBeLessThanOrEqual(390)
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({ path: info.outputPath('mobile-download-options.png'), fullPage: true })
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '批量搜索', exact: true }).click()
  await page.getByLabel('选择批量查询图片').setInputFiles([upload('first.png'), upload('second.png'), upload('third.png')])
  await expect(page.locator('.batch-job')).toHaveCount(3)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.screenshot({ path: info.outputPath('mobile-batch-queue.png'), fullPage: true })
})
