import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { strFromU8, unzipSync } from 'fflate'

// Controlled API fixtures: no writes, model calls, or downloads reach a real service.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const upload = (name: string) => ({ name, mimeType: 'image/png', buffer: png })
const jsonFile = (value: unknown) => ({ name: 'config.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(value)) })
const category = (id: number, code: string, name: string, parent: number | null = null, active = true) => ({
  id, code, name, parent_id: parent, path: parent ? `${parent}.${id}` : `${id}`, names: [{ lang: 'zh', name, kind: 'primary' }],
  cuisines: [], heating_group: null, sort_order: 0, is_active: active, image_count: 0, scene_rule_count: 0,
})
const categories = [category(1, 'dish', '菜品'), category(2, 'dish.pizza', '披萨<&', 1, false)]
const scene = { id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false,
  is_active: true, member_count: 0, created_at: null, labels: [], rules: [] }
const hit = { tags: '标签样本', description: '描述样本', original_url: 'https://fixture.test/image.png', similarity: 0.91,
  details: { id: '00000000-0000-4000-8000-000000000001', category_id: 2, category: '披萨', scenes: ['general'], scene_label: '场景样本',
    label_status: 'verified', images: [{ image_url: 'https://fixture.test/image.png', thumbnail_url: 'https://fixture.test/image.png', width: 10, height: 10 }] } }

async function mockApi(page: Page) {
  await page.addInitScript(() => localStorage.setItem('food-atlas-api', ''))
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/categories') return route.fulfill({ json: { ok: true, categories, root_codes: ['dish'], heating_groups: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, default_scene: 'general', scenes: [scene] } })
    return route.fulfill({ status: 404, json: { ok: false, error: 'UNEXPECTED_FIXTURE_REQUEST' } })
  })
  await page.route('https://fixture.test/**', route => route.fulfill({ contentType: 'image/png', body: png }))
}

async function downloadBytes(page: Page, info: TestInfo, action: () => Promise<unknown>, name: string) {
  const pending = page.waitForEvent('download')
  await action()
  const download = await pending
  const path = info.outputPath(name)
  await download.saveAs(path)
  expect(await download.failure()).toBeNull()
  return readFile(path)
}

test.beforeEach(async ({ page }) => mockApi(page))

test('类别导出 ZIP 含完整 JSON、转义后的 SVG、有效 PNG 和文字拓扑', async ({ page }, info) => {
  await page.goto('/?view=categories')
  await page.getByRole('button', { name: '下载类别树', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '下载类别树', exact: true })
  const bytes = await downloadBytes(page, info, () => dialog.getByRole('button', { name: '打包下载' }).click(), 'category.zip')
  const files = unzipSync(new Uint8Array(bytes))
  expect(Object.keys(files).sort()).toEqual(['README.txt', 'category-topology.png', 'category-topology.svg', 'category-topology.txt', 'category-tree.json'])
  const document = JSON.parse(strFromU8(files['category-tree.json']))
  expect(document).toMatchObject({ kind: 'category_tree', category_count: 2 })
  expect(document.categories[0].children[0]).toMatchObject({ code: 'dish.pizza', is_active: false })
  expect(strFromU8(files['category-topology.svg'])).toContain('披萨&lt;&amp;')
  expect(strFromU8(files['category-topology.txt'])).toContain('披萨<& [dish.pizza]')
  expect(Array.from(files['category-topology.png'].slice(0, 8))).toEqual([137, 80, 78, 71, 13, 10, 26, 10])
})

test('PNG 生成失败时 ZIP 保留其余文件并提示部分文件没有生成', async ({ page }, info) => {
  // Exercise the browser's real export path, making only the canvas encoder fail.
  await page.addInitScript(() => {
    Object.defineProperty(HTMLCanvasElement.prototype, 'toBlob', {
      configurable: true,
      value(callback: BlobCallback) { callback(null) },
    })
  })
  await page.goto('/?view=categories')
  await page.getByRole('button', { name: '下载类别树', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '下载类别树', exact: true })
  const bytes = await downloadBytes(page, info, () => dialog.getByRole('button', { name: '打包下载' }).click(), 'category-without-png.zip')
  const files = unzipSync(new Uint8Array(bytes))
  expect(Object.keys(files).sort()).toEqual(['README.txt', 'category-topology.svg', 'category-topology.txt', 'category-tree.json'])
  expect(JSON.parse(strFromU8(files['category-tree.json']))).toMatchObject({ kind: 'category_tree', category_count: 2 })
  expect(strFromU8(files['category-topology.svg'])).toContain('披萨&lt;&amp;')
  expect(strFromU8(files['category-topology.txt'])).toContain('披萨<& [dish.pizza]')
  expect(strFromU8(files['README.txt'])).toContain('食物类别导出包')
  const warning = page.getByRole('alertdialog')
  await expect(warning).toContainText('部分文件没有生成')
  await expect(warning).toContainText('压缩包已发起下载，但里面没有 PNG：PNG 生成失败')
  await page.screenshot({ path: info.outputPath('png-fallback.png'), fullPage: true })
})

test('类别上传先预览，确认才写入，部分错误仍显示；坏 JSON 不发送请求', async ({ page }) => {
  const calls: boolean[] = []
  await page.route('**/api/categories/import?*', route => {
    const dryRun = new URL(route.request().url()).searchParams.get('dry_run') === 'true'
    calls.push(dryRun)
    return route.fulfill({ json: { ok: true, dry_run: dryRun,
      summary: { total: 2, create: 1, update: 0, delete: 0, unchanged: 0, error: 1, not_in_file: 0 },
      rows: [{ kind: 'category', key: 'dish.new', action: 'create', name: '新类别', changes: [] },
        { kind: 'category', key: 'bad', action: 'error', detail: '编码有问题', error: 'CATEGORY_BAD_CODE' }],
    } })
  })
  await page.goto('/?view=categories')
  await page.getByLabel('选择类别树文件').setInputFiles(jsonFile({ kind: 'category_tree', categories: [] }))
  const dialog = page.getByRole('dialog', { name: '上传类别树', exact: true })
  await expect(dialog.getByText('有问题的 1 项不会导入，其余 1 项照常导入。', { exact: true })).toBeVisible()
  expect(calls.every(Boolean)).toBe(true)
  await dialog.getByRole('button', { name: '确认导入（1 项）', exact: true }).click()
  await expect(dialog.getByText('已导入 1 项，另有 1 项没有导入（见下表）。改好文件后重新上传即可，已导入的不会重复。')).toBeVisible()
  expect(calls.filter(value => !value)).toHaveLength(1)
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  const previous = calls.length
  await page.getByLabel('选择类别树文件').setInputFiles({ name: 'bad.json', mimeType: 'application/json', buffer: Buffer.from('{broken') })
  await expect(page.getByRole('alertdialog')).toContainText('不是有效的 JSON')
  expect(calls).toHaveLength(previous)
})

test('场景下载可回传，prune 改变后重新预览，确认才发送 prune 写入', async ({ page }, info) => {
  const calls: { dryRun: boolean; prune: boolean; body: unknown }[] = []
  await page.route('**/api/scenes/import?*', route => {
    const params = new URL(route.request().url()).searchParams
    const dryRun = params.get('dry_run') === 'true'
    const prune = params.get('prune') === 'true'
    calls.push({ dryRun, prune, body: route.request().postDataJSON() })
    return route.fulfill({ json: { ok: true, dry_run: dryRun, prune, sync_required: true,
      summary: { total: 1, update: 1, create: 0, delete: prune ? 1 : 0, error: 0, unchanged: 0, not_in_file: 0 },
      rows: [{ kind: 'scene', key: 'general', action: 'update', changes: [] },
        ...(prune ? [{ kind: 'label', key: 'general/old', action: 'delete', changes: [] }] : [])],
    } })
  })
  await page.goto('/?view=scenes')
  const bytes = await downloadBytes(page, info, () => page.getByRole('button', { name: '下载配置', exact: true }).click(), 'scenes.json')
  const document = JSON.parse(bytes.toString('utf8'))
  expect(document).toMatchObject({ kind: 'scene_config', scene_count: 1, scenes: [{ code: 'general' }] })
  await page.getByLabel('选择场景配置文件').setInputFiles(jsonFile(document))
  const dialog = page.getByRole('dialog', { name: '上传场景配置', exact: true })
  await expect(dialog.getByRole('button', { name: '确认导入（1 项）', exact: true })).toBeEnabled()
  await dialog.getByRole('checkbox').check()
  await expect(dialog.getByRole('button', { name: '确认导入（2 项）', exact: true })).toBeEnabled()
  expect(calls.every(call => call.dryRun)).toBe(true)
  expect(calls.some(call => call.prune)).toBe(true)
  await dialog.getByRole('button', { name: '确认导入（2 项）', exact: true }).click()
  await expect(dialog.getByText('这些改动要「全量同步」后才作用到已入库的图片，检索也是。')).toBeVisible()
  expect(calls.filter(call => !call.dryRun)).toEqual([{ dryRun: false, prune: true, body: document }])
})

test('自建库批量走 files 接口，逐图失败可重试，卡片分别显示类别、场景和标签', async ({ page }) => {
  const filenames: string[][] = []
  await page.route('**/api/search/batch/files', route => {
    const names = [...(route.request().postDataBuffer()?.toString() ?? '').matchAll(/filename="([^"]+)"/g)].map(match => match[1])
    filenames.push(names)
    return route.fulfill({ json: { ok: true, scene: 'general', outputs_heating: false, results: names.map((input, index) =>
      filenames.length === 1 && input === 'bad.png'
        ? { index, input, ok: false, error: 'SEARCH_BAD_IMAGE', detail: 'bad query' }
        : { index, input, ok: true, source: 'self', items: [hit], elapsed_ms: 50 }),
    } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '批量搜索', exact: true }).click()
  await page.getByLabel('选择批量查询图片').setInputFiles([upload('good.png'), upload('bad.png')])
  await page.getByRole('button', { name: '开始批量搜索', exact: true }).click()
  await expect(page.locator('p.batch-job-error')).toContainText('SEARCH_BAD_IMAGE')
  await expect(page.locator('.result-card').first()).toContainText('披萨')
  await expect(page.locator('.result-card').first()).toContainText('场景样本')
  await expect(page.locator('.result-card').first()).toContainText('标签样本')
  await page.getByRole('button', { name: '重试未完成', exact: true }).click()
  await expect(page.locator('.batch-job-success')).toHaveCount(2)
  expect(filenames).toEqual([['good.png', 'bad.png'], ['bad.png']])
})

test('自建库批量停止忽略旧响应，继续重新请求停止的图片', async ({ page }) => {
  let release = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  let finishOld = () => {}
  const oldFinished = new Promise<void>(resolve => { finishOld = resolve })
  let calls = 0
  await page.route('**/api/search/batch/files', async route => {
    const call = ++calls
    if (call === 1) await gate
    await route.fulfill({ json: { ok: true, scene: 'general', results: [{ index: 0, input: 'query.png', ok: true, source: 'self', items: [{ ...hit, tags: call === 1 ? '旧响应' : '恢复结果' }] }] } }).catch(() => {})
    if (call === 1) finishOld()
  })
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '批量搜索', exact: true }).click()
    await page.getByLabel('选择批量查询图片').setInputFiles(upload('query.png'))
    await page.getByRole('button', { name: '开始批量搜索', exact: true }).click()
    await expect.poll(() => calls).toBe(1)
    await page.getByRole('button', { name: '停止批量搜索', exact: true }).click()
    await expect(page.getByText('已停止，可继续', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '继续搜索', exact: true }).click()
    await expect(page.locator('.result-card')).toContainText('恢复结果')
    release()
    await oldFinished
    await expect(page.locator('.result-card')).not.toContainText('旧响应')
    expect(calls).toBe(2)
  } finally { release() }
})

test('业务搜图传递兜底开关，兜底失败作为搜索提示显示', async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/search/auto/url', route => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { ok: true, source: 'empty', scene: 'general', items: [],
      fallback: bodies.at(-1)?.global_fallback
        ? { status: 'failed', error: 'GLOBAL_FAILED', detail: 'controlled failure' }
        : { status: 'disabled' } } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '业务搜图', exact: true }).click()
  await page.getByRole('button', { name: '图片链接', exact: true }).click()
  await page.getByLabel('图片地址').fill('https://fixture.test/query.png')
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toContainText('Global 兜底失败')
  await page.getByRole('alertdialog').getByRole('button', { name: '知道了' }).click()
  await page.getByRole('checkbox', { name: '自建库没有结果时，用现网 Global 兜底' }).uncheck()
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
  await expect.poll(() => bodies.length).toBe(2)
  expect(bodies).toEqual([
    expect.objectContaining({ image_url: 'https://fixture.test/query.png', global_fallback: true, top_k: 10 }),
    expect.objectContaining({ image_url: 'https://fixture.test/query.png', global_fallback: false, top_k: 10 }),
  ])
})
