import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { acknowledgeNotice, expectToast } from './notice'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const CATEGORIES = [{
  id: 1, code: 'dish.steak', parent_id: null, path: '1', name: '牛排', names: [{ lang: 'zh', name: '牛排', kind: 'primary' }], cuisines: [],
  heating_group: null, sort_order: 0, is_active: true, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
}]
const SCENES = [{ id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] }]
const ENUMS = [{ name: 'food_state', label: '食物状态', editable: true, values: [{ value: 'raw', label: '生' }, { value: 'cooked', label: '熟' }] }]

interface Stored { n: number; id: string; categoryId: number | null; labelStatus: string; description: string; tags: string[]; foodState: string | null; deleted: boolean }

/** Image 3 has no category; odd ones are tagged 带骨. */
function makeImages(count: number): Stored[] {
  return Array.from({ length: count }, (_, index) => {
    const n = index + 1
    return { n, id: imageId(n), categoryId: n === 3 ? null : 1, labelStatus: n === 3 ? 'unlabeled' : 'auto', description: `图片${n}`, tags: n % 2 ? ['带骨'] : [], foodState: null, deleted: false }
  })
}

const listItem = (image: Stored) => ({
  id: image.id, original_url: `https://cdn.test/o/${image.n}.png`, thumb_url: `https://cdn.test/t/${image.n}.png`,
  category_id: image.categoryId, category: image.categoryId ? '牛排' : null, scenes: ['general'], label_status: image.labelStatus,
  description: image.description, tags: image.tags, width: 640, height: 480, created_at: '2026-11-10T08:00:00+00:00', updated_at: null, vector_status: 'synced',
})
const detail = (image: Stored) => ({
  ...listItem(image), sha256: 'ab'.repeat(32), category_code: image.categoryId ? 'dish.steak' : null, source_type: 'user_upload', license: 'unknown',
  food_state: image.foodState, food_processing: null, food_count: null, total_mass_g: null, water_mass_g: null, main_ingredient_mass_g: null,
  heating_group: null, payload_version: 2, embeddings: [],
})
const vector = { status: 'synced', collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: true, payload: {}, expected_payload: {}, payload_matches: true, payload_version: 2, synced_version: 2, embedded_at: null }

/** failOnce: ids whose first PATCH / DELETE answers 503; total: what the list says it has (default: what is stored). */
async function mockApi(page: Page, { count = 30, failOnce = [] as string[], total = null as number | null } = {}) {
  const images = makeImages(count)
  const failing = new Set(failOnce)
  const calls = { list: [] as Array<Record<string, string>>, patches: [] as Array<{ id: string; body: Record<string, unknown> }>, deletes: [] as string[], details: [] as string[] }
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: ENUMS } })
    if (path === '/api/categories') return route.fulfill({ json: { ok: true, categories: CATEGORIES, heating_groups: [], root_codes: ['dish'] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
    if (path === '/api/images' && method === 'GET') {
      calls.list.push(Object.fromEntries(url.searchParams))
      const size = Number(url.searchParams.get('page_size'))
      const at = Number(url.searchParams.get('page'))
      const labelStatus = url.searchParams.get('label_status')
      const visible = images.filter(image => !image.deleted && (!labelStatus || image.labelStatus === labelStatus))
      return route.fulfill({ json: { ok: true, trace_id: 't', scene: 'general', page: at, page_size: size, total: total ?? visible.length, items: visible.slice((at - 1) * size, at * size).map(listItem) } })
    }
    const image = images.find(entry => path === `/api/images/${entry.id}`)
    if (image) {
      if (image.deleted) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 't' } })
      if (failing.has(image.id) && (method === 'PATCH' || method === 'DELETE')) {
        failing.delete(image.id)
        // Every attempt is recorded, the refused one too
        if (method === 'PATCH') calls.patches.push({ id: image.id, body: request.postDataJSON() as Record<string, unknown> })
        else calls.deletes.push(image.id)
        return route.fulfill({ status: 503, json: { ok: false, error: 'NOT_READY', detail: 'retrieval stack is not initialized', trace_id: 'e2e-batch' } })
      }
      if (method === 'GET') {
        calls.details.push(image.id)
        return route.fulfill({ json: { ok: true, image: detail(image), vector } })
      }
      if (method === 'PATCH') {
        const body = request.postDataJSON() as Record<string, unknown>
        calls.patches.push({ id: image.id, body })
        if ('category_id' in body) image.categoryId = body.category_id as number | null
        if ('label_status' in body) image.labelStatus = body.label_status as string
        if ('tags' in body) image.tags = body.tags as string[]
        if ('description' in body) image.description = (body.description as string | null) ?? ''
        if ('food_state' in body) image.foodState = body.food_state as string | null
        return route.fulfill({ json: { ok: true, image: detail(image), vector, vector_sync: 'pushed', sync_required: false } })
      }
      if (method === 'DELETE') {
        calls.deletes.push(image.id)
        image.deleted = true
        return route.fulfill({ json: { ok: true, image_id: image.id, deleted_at: '2026-11-11T09:00:00+00:00', cleanup: 'done' } })
      }
    }
    return route.abort('blockedbyclient')
  })
  await page.route('https://cdn.test/**', route => route.fulfill({ contentType: 'image/png', body: png }))
  return { calls, images }
}

async function openLibrary(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^图片数据/ }).click()
  await expect(page.locator('.mg-library-card').first()).toBeVisible()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(name + '.png'), animations: 'disabled' })
}

const pick = (page: Page, n: number) => page.getByRole('checkbox', { name: `选择 图片${n}`, exact: true })
const bar = (page: Page) => page.getByRole('toolbar', { name: '批量操作' })

test('批量修改：勾选（Shift 连选），只发改了的字段；没有类别的图片不改标注状态', async ({ page }, info) => {
  const { calls } = await mockApi(page)
  await openLibrary(page)
  await pick(page, 1).click()
  await pick(page, 4).click({ modifiers: ['Shift'] })
  await expect(bar(page)).toContainText('已选 4 张')
  await expect(page.locator('.mg-library-card.is-selected')).toHaveCount(4)
  await screenshot(page, info, 'batch-selected')

  await bar(page).getByRole('button', { name: '批量修改' }).click()
  const dialog = page.getByRole('dialog', { name: '批量修改标注（4 张）' })
  await expect(dialog.getByRole('button', { name: '开始修改' })).toBeDisabled()  // nothing to change yet
  await dialog.getByLabel('标签：怎么改').selectOption('add')
  const tags = dialog.getByLabel('要添加的标签')
  await tags.fill('批量')
  await tags.press('Enter')
  await dialog.getByLabel('标注状态：怎么改').selectOption('verified')
  await dialog.getByLabel('食物状态：怎么改').selectOption('set')
  await dialog.getByLabel('新的食物状态').selectOption('raw')
  await expect(dialog.locator('.lb-summary')).toHaveText('将修改 4 张图片的：标注状态、标签、食物状态')
  await screenshot(page, info, 'batch-edit-form')
  const listsBefore = calls.list.length
  await dialog.getByRole('button', { name: '开始修改' }).click()

  // Image 3 has no category: its tags and food state change, its status cannot
  await expect(dialog.getByRole('region', { name: '批量修改进度' })).toContainText('修改完成')
  await expect(dialog.getByRole('list', { name: '需要留意的图片' })).toContainText('图片3备注：没有类别，标注状态没改')
  await screenshot(page, info, 'batch-edit-result')
  expect(calls.details.sort()).toEqual([1, 2, 3, 4].map(imageId))  // tags are added to what each image has now
  expect(Object.fromEntries(calls.patches.map(call => [call.id, call.body]))).toEqual({
    [imageId(1)]: { label_status: 'verified', tags: ['带骨', '批量'], food_state: 'raw' },
    [imageId(2)]: { label_status: 'verified', tags: ['批量'], food_state: 'raw' },
    [imageId(3)]: { tags: ['带骨', '批量'], food_state: 'raw' },
    [imageId(4)]: { label_status: 'verified', tags: ['批量'], food_state: 'raw' },
  })
  await expect.poll(() => calls.list.length).toBeGreaterThan(listsBefore)  // the page reloads
  await dialog.getByRole('button', { name: '完成' }).click()
  await expect(dialog).toBeHidden()
  await expect(bar(page)).toContainText('已选 4 张')  // still picked for the next change
  await expect(page.locator('.mg-library-card').first()).toContainText('#批量')
})

test('批量修改：失败的可以重试；全部成功后自动关闭并提示', async ({ page }) => {
  const { calls } = await mockApi(page, { failOnce: [imageId(2)] })
  await openLibrary(page)
  await bar(page).getByRole('checkbox', { name: '选择本页' }).check()
  await expect(bar(page)).toContainText('已选 24 张')
  await bar(page).getByRole('button', { name: '批量修改' }).click()
  const dialog = page.getByRole('dialog', { name: '批量修改标注（24 张）' })
  await dialog.getByLabel('描述：怎么改').selectOption('set')
  await dialog.getByLabel('新的描述').fill('统一描述')
  await dialog.getByRole('button', { name: '开始修改' }).click()
  const progress = dialog.getByRole('region', { name: '批量修改进度' })
  await expect(progress).toContainText('修改完成，有 1 张没成功')
  await expect(progress).toContainText('成功 23')
  await expect(dialog.getByRole('list', { name: '需要留意的图片' })).toContainText('结果不确定')
  await dialog.getByRole('button', { name: '重试失败的（1）' }).click()
  await expectToast(page, '已修改 24 张图片的描述。', 'success')
  await expect(dialog).toBeHidden()
  expect(calls.patches.filter(call => call.id === imageId(2))).toHaveLength(2)
  expect(new Set(calls.patches.map(call => JSON.stringify(call.body)))).toEqual(new Set([JSON.stringify({ description: '统一描述' })]))
})

test('批量删除：选择全部筛选结果，数量多时输入「删除」确认；删完从列表和选择里去掉', async ({ page }, info) => {
  const { calls } = await mockApi(page, { failOnce: [imageId(7)] })
  await openLibrary(page)
  await bar(page).getByRole('button', { name: '选择全部 30 张' }).click()
  await expect(bar(page)).toContainText('已选 30 张')
  expect(calls.list.at(-1)).toMatchObject({ page: '1', page_size: '100' })

  await bar(page).getByRole('button', { name: '批量删除' }).click()
  const dialog = page.getByRole('dialog', { name: '批量删除（30 张）' })
  await expect(dialog).toContainText('无法恢复')
  const remove = dialog.getByRole('button', { name: '删除 30 张' })
  await expect(remove).toBeDisabled()
  await dialog.getByLabel('输入「删除」确认').fill('删除')
  await screenshot(page, info, 'batch-delete-confirm')
  await remove.click()
  await expect(dialog.getByRole('region', { name: '批量删除进度' })).toContainText('有 1 张没成功')
  await dialog.getByRole('button', { name: '重试失败的（1）' }).click()
  await expectToast(page, '已删除 30 张图片。', 'success')
  await expect(dialog).toBeHidden()
  expect(new Set(calls.deletes).size).toBe(30)
  await expect(page.getByRole('heading', { name: '暂无已入库图片（空库）' })).toBeVisible()
  await expect(bar(page)).toHaveCount(0)
})

test('少量删除不用输入确认；换筛选条件会清空选择', async ({ page }) => {
  const { calls } = await mockApi(page)
  await openLibrary(page)
  await pick(page, 2).click()
  await bar(page).getByRole('button', { name: '批量删除' }).click()
  const dialog = page.getByRole('dialog', { name: '批量删除（1 张）' })
  await expect(dialog.getByLabel('输入「删除」确认')).toHaveCount(0)
  await dialog.getByRole('button', { name: '删除 1 张' }).click()
  await expectToast(page, '已删除 1 张图片。', 'success')
  expect(calls.deletes).toEqual([imageId(2)])
  await expect(bar(page)).toContainText('可勾选图片后批量操作')

  await pick(page, 1).click()
  await expect(bar(page)).toContainText('已选 1 张')
  await page.getByRole('radiogroup', { name: '标注状态' }).getByRole('radio', { name: '自动标注' }).check()
  await expect.poll(() => calls.list.at(-1)?.label_status).toBe('auto')
  await expect(bar(page)).toContainText('可勾选图片后批量操作')  // another result set: nothing stays picked
})

test('选择全部：超过 1000 张时请先缩小筛选范围', async ({ page }) => {
  await mockApi(page, { total: 1500 })
  await openLibrary(page)
  await bar(page).getByRole('button', { name: '选择全部 1,500 张' }).click()
  await acknowledgeNotice(page, '一次最多选择 1000 张', 'warn')
  await expect(bar(page)).toContainText('可勾选图片后批量操作')
})
