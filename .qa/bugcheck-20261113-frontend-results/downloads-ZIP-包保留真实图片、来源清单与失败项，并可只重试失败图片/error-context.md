# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: downloads.spec.ts >> ZIP 包保留真实图片、来源清单与失败项，并可只重试失败图片
- Location: e2e\downloads.spec.ts:52:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.setInputFiles: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByLabel('选择查询图片', { exact: true })

```

# Test source

```ts
  1   | import { expect, test, type Page, type TestInfo } from '@playwright/test'
  2   | import { readFile } from 'node:fs/promises'
  3   | import { strFromU8, unzipSync } from 'fflate'
  4   | 
  5   | // All remote requests and the directory picker are controlled fixtures.
  6   | const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
  7   | const upload = (name: string) => ({ name, mimeType: 'image/png', buffer: png })
  8   | const rows = [
  9   |   { title: '番茄:红', image_url: 'https://download-fixture.test/tomato.png', similarity: 0.91 },
  10  |   { title: '黄瓜', image_url: 'https://download-fixture.test/cucumber.png', similarity: 0.82 },
  11  |   { title: '失败图片', image_url: 'https://download-fixture.test/failed.png', similarity: 0.73 },
  12  | ]
  13  | 
  14  | async function searchAndSelect(page: Page, selectedRows = rows) {
  15  |   await page.route('**/api/predict/searchImage_file', route => route.fulfill({ json: { result: selectedRows } }))
> 16  |   await page.getByLabel('选择查询图片', { exact: true }).setInputFiles(upload('query.png'))
      |   ^ Error: locator.setInputFiles: Test timeout of 30000ms exceeded.
  17  |   await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  18  |   await expect(page.locator('.result-card')).toHaveCount(selectedRows.length)
  19  |   await page.getByRole('button', { name: '选择全部结果', exact: true }).click()
  20  |   await page.getByRole('button', { name: /^批量下载（/ }).click()
  21  |   return page.getByRole('dialog', { name: '下载所选图片', exact: true })
  22  | }
  23  | 
  24  | async function archiveEntries(page: Page, info: TestInfo, action: () => Promise<unknown>, name: string) {
  25  |   const waiting = page.waitForEvent('download')
  26  |   await action()
  27  |   const download = await waiting
  28  |   expect(download.suggestedFilename()).toMatch(/^images-.*\.zip$/)
  29  |   const path = info.outputPath(name)
  30  |   await download.saveAs(path)
  31  |   expect(await download.failure()).toBeNull()
  32  |   return unzipSync(new Uint8Array(await readFile(path)))
  33  | }
  34  | 
  35  | function gate() {
  36  |   let release: () => void = () => {}
  37  |   const ready = new Promise<void>(resolve => { release = resolve })
  38  |   return { ready, release }
  39  | }
  40  | 
  41  | test.beforeEach(async ({ page }) => {
  42  |   await page.addInitScript(() => localStorage.setItem('food-atlas-api', ''))
  43  |   await page.route('**/api/**', route => route.request().url().endsWith('/api/ping')
  44  |     ? route.fulfill({ json: { ok: true } })
  45  |     : route.fulfill({ status: 404, json: { detail: 'Controlled endpoint not available' } }))
  46  |   await page.route('https://download-fixture.test/**', route => route.fulfill({
  47  |     contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png,
  48  |   }))
  49  |   await page.goto('/')
  50  | })
  51  | 
  52  | test('ZIP 包保留真实图片、来源清单与失败项，并可只重试失败图片', async ({ page }, info) => {
  53  |   let failedAvailable = false
  54  |   await page.route('https://download-fixture.test/failed.png', route => {
  55  |     if (route.request().resourceType() === 'image' || failedAvailable) {
  56  |       return route.fulfill({ contentType: 'image/png', headers: { 'access-control-allow-origin': '*' }, body: png })
  57  |     }
  58  |     return route.fulfill({ status: 404, headers: { 'access-control-allow-origin': '*' }, body: 'Controlled missing image' })
  59  |   })
  60  |   const dialog = await searchAndSelect(page)
  61  |   const files = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '开始下载', exact: true }).click(), 'partial-results.zip')
  62  |   expect(Object.keys(files).sort()).toEqual(['0001-番茄_红.png', '0002-黄瓜.png', 'results.json'].sort())
  63  |   expect(Buffer.from(files['0001-番茄_红.png'])).toEqual(png)
  64  |   expect(Buffer.from(files['0002-黄瓜.png'])).toEqual(png)
  65  |   const manifest = JSON.parse(strFromU8(files['results.json']))
  66  |   expect(manifest.results).toHaveLength(2)
  67  |   expect(manifest.results[0]).toMatchObject({ title: '番茄:红', source: rows[0].image_url, similarity: 0.91, bytes: png.length })
  68  |   expect(manifest.failures).toEqual([expect.objectContaining({ title: '失败图片', imageUrl: rows[2].image_url, reason: 'HTTP 404' })])
  69  |   await expect(dialog.getByRole('heading', { name: '失败记录 · 1', exact: true })).toBeVisible()
  70  |   await expect(dialog.getByText('已将含 2 张图片及来源清单的 ZIP 交给浏览器，请查看下载列表确认保存结果。', { exact: true })).toBeVisible()
  71  |   await page.screenshot({ path: info.outputPath('desktop-download-partial.png'), fullPage: true })
  72  |   failedAvailable = true
  73  |   const retried = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '重试失败项（1）', exact: true }).click(), 'retried-failure.zip')
  74  |   expect(Object.keys(retried).sort()).toEqual(['0001-失败图片.png', 'results.json'].sort())
  75  |   const retryManifest = JSON.parse(strFromU8(retried['results.json']))
  76  |   expect(retryManifest.results).toEqual([expect.objectContaining({ id: 'image-3', title: '失败图片' })])
  77  |   expect(retryManifest.failures).toEqual([])
  78  |   await expect(dialog.getByRole('heading', { name: '失败记录 · 1', exact: true })).toHaveCount(0)
  79  | })
  80  | 
  81  | test('来源拒绝跨域下载时通过后端下载接口获取图片', async ({ page }, info) => {
  82  |   const proxyUrls: string[] = []
  83  |   await page.route('https://download-fixture.test/tomato.png', route => route.request().resourceType() === 'image'
  84  |     ? route.fulfill({ contentType: 'image/png', body: png })
  85  |     : route.abort('accessdenied'))
  86  |   await page.route('**/api/images/download?*', route => {
  87  |     proxyUrls.push(new URL(route.request().url()).searchParams.get('url') ?? '')
  88  |     return route.fulfill({ contentType: 'image/png', body: png })
  89  |   })
  90  |   const dialog = await searchAndSelect(page, [rows[0]])
  91  |   const files = await archiveEntries(page, info, () => dialog.getByRole('button', { name: '开始下载', exact: true }).click(), 'proxy-result.zip')
  92  |   expect(proxyUrls).toEqual([rows[0].image_url])
  93  |   expect(Buffer.from(files['0001-番茄_红.png'])).toEqual(png)
  94  |   const manifest = JSON.parse(strFromU8(files['results.json']))
  95  |   expect(manifest.results[0].source).toBe(rows[0].image_url)
  96  |   expect(manifest.failures).toEqual([])
  97  | })
  98  | 
  99  | test('选择目录只写入用户选择的新子目录，并保存来源清单', async ({ page }, info) => {
  100 |   await page.addInitScript(() => {
  101 |     const capture = { picked: 0, directories: [] as string[], files: {} as Record<string, number[]> }
  102 |     Object.assign(window, { __testDirectory: capture })
  103 |     function directory(name: string, prefix = name): unknown {
  104 |       const children = new Map<string, unknown>()
  105 |       return {
  106 |         name,
  107 |         async getDirectoryHandle(child: string, options?: { create?: boolean }) {
  108 |           if (children.has(child)) return children.get(child)
  109 |           if (!options?.create) throw new DOMException('Controlled missing directory', 'NotFoundError')
  110 |           const next = directory(child, `${prefix}/${child}`)
  111 |           children.set(child, next)
  112 |           capture.directories.push(`${prefix}/${child}`)
  113 |           return next
  114 |         },
  115 |         async getFileHandle(filename: string) {
  116 |           return { async createWritable() {
```