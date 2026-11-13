import { expect, test, type Page, type Request, type TestInfo } from '@playwright/test'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const upload = (name: string) => ({ name, mimeType: 'image/png', buffer: png })

const category = (id: number, code: string, parent: number | null, name: string, cuisines: string[] = [], isActive = true) => ({
  id, code, parent_id: parent, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }], cuisines,
  heating_group: null, sort_order: 0, is_active: isActive, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
})
const CATEGORIES = {
  ok: true,
  categories: [
    category(1, 'dish', null, '菜品', ['chinese']),
    category(2, 'dish.meat', 1, '肉类'),
    category(3, 'dish.meat.steak', 2, '牛排', ['american']),
    category(4, 'dish.meat.pork', 2, '红烧肉'),
    category(5, 'dish.retired', 1, '停用类别', [], false),
  ],
  heating_groups: [],
  root_codes: ['ingredient', 'dish', 'dessert', 'beverage', 'mixed'],
}
const scene = (id: number, code: string, name: string, rules: unknown[], includeAll = false) => ({
  id, code, name, description: null, include_all: includeAll, outputs_heating: false, is_active: true,
  created_at: null, member_count: 0, labels: [], rules,
})
const rule = (id: number, categoryId: number | null, cuisine: string | null) => ({
  id, category: categoryId === null ? null : { id: categoryId, code: `c${categoryId}`, name: null }, cuisine, label: null,
})
const SCENES = {
  ok: true,
  default_scene: 'general',
  scenes: [
    scene(1, 'general', '通用', [], true),
    scene(2, 'oven', '一体机', [rule(1, 2, null)]),
    scene(3, 'cn_diet', '中国饮食', [rule(2, null, 'chinese')]),
    scene(4, 'us_diet', '美国饮食', [rule(3, null, 'american')]),
  ],
}
const enumItem = (name: string, values: Array<[string, string]>) => ({ name, label: name, editable: true, values: values.map(([value, label]) => ({ value, label })) })
const ENUMS = {
  ok: true,
  enums: [
    enumItem('food_state', [['raw', '生'], ['cooked', '熟'], ['frozen', '冷冻']]),
    enumItem('food_processing', [['sliced', '切片'], ['whole', '整个']]),
    enumItem('source_type', [['user_upload', '用户上传'], ['crawled', '爬取']]),
    enumItem('license', [['unknown', '未知'], ['commercial', '可商用']]),
  ],
}
// What the server's rules give each category (the mock of POST /api/index answers with it)
const RULE_SCENES: Record<string, string[]> = {
  'dish.meat.steak': ['general', 'oven', 'us_diet'],
  'dish.meat.pork': ['general', 'oven', 'cn_diet'],
}

type IndexCall = { fields: Record<string, string>; filename: string }

async function mockApi(page: Page, { created = true }: { created?: boolean } = {}) {
  const indexCalls: IndexCall[] = []
  const membershipCalls: Array<{ path: string; body: unknown }> = []
  // Only the backend: Vite also serves source modules under paths such as /src/shared/api/
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    if (pathname.endsWith('/api/ping')) return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (pathname.endsWith('/api/enums')) return route.fulfill({ json: ENUMS })
    if (pathname.endsWith('/api/categories')) return route.fulfill({ json: CATEGORIES })
    if (pathname.endsWith('/api/scenes')) return route.fulfill({ json: SCENES })
    if (pathname.endsWith('/api/index') && request.method() === 'POST') {
      const call = await formFields(request)
      indexCalls.push(call)
      const id = `00000000-0000-4000-8000-00000000000${indexCalls.length}`
      return route.fulfill({ json: { ok: true, trace_id: 't', failed: [], indexed: [{
        id, filename: call.filename, original_url: `https://cdn.test/o/${id}.png`, thumb_url: `https://cdn.test/t/${id}.webp`,
        category_id: null, scenes: RULE_SCENES[call.fields.category] ?? ['general'], label_status: call.fields.label_status ?? 'unlabeled',
        food_state: call.fields.food_state ?? null, description: call.fields.description ?? '', tags: [], width: 1, height: 1,
        payload_version: 1, created_at: null, created,
      }] } })
    }
    const membership = pathname.match(/\/api\/images\/([^/]+)\/scene-memberships\/(\d+)$/)
    if (membership && request.method() === 'PUT') {
      membershipCalls.push({ path: `${membership[1]}/${membership[2]}`, body: request.postDataJSON() })
      return route.fulfill({ json: {
        ok: true, image_id: membership[1], payload_version: 2, scenes: ['general', 'oven', 'cn_diet', 'us_diet'], memberships: [],
        vector_sync: 'pushed', sync_required: false,
      } })
    }
    return route.abort('blockedbyclient')
  })
  return { indexCalls, membershipCalls }
}

/** The multipart text fields of an index request (the file part is left out). */
async function formFields(request: Request): Promise<IndexCall> {
  const body = request.postDataBuffer()?.toString('latin1') ?? ''
  const fields: Record<string, string> = {}
  let filename = ''
  for (const part of body.split(/--[-\w]+\r\n/)) {
    const name = part.match(/name="([^"]+)"/)?.[1]
    if (!name) continue
    const file = part.match(/filename="([^"]*)"/)
    if (file) { filename = file[1]; continue }
    const value = part.split('\r\n\r\n')[1]?.replace(/\r\n(--[-\w]+--\r\n)?$/, '') ?? ''
    fields[name] = Buffer.from(value, 'latin1').toString('utf8')
  }
  return { fields, filename }
}

async function openImport(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^上传与导入/ }).click()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

const batch = (page: Page) => page.locator('.mg-import-settings')

async function pickCategory(scope: ReturnType<Page['locator']>, query: string, option: string) {
  const box = scope.getByRole('combobox', { name: '类别' })
  await box.fill(query)
  await scope.getByRole('option', { name: new RegExp(`^${option}`) }).click()
}

test('批量标注：类别来自数据库，场景按规则预览并可人工调整，所有属性随入库提交', async ({ page }, info) => {
  const api = await mockApi(page)
  await openImport(page)
  await page.getByLabel('选择待入库图片').setInputFiles([upload('steak.png'), upload('pork.png')])
  await expect(page.locator('.mg-upload-card')).toHaveCount(2)

  // Category: only active categories from GET /api/categories, searched by name
  const form = batch(page)
  await form.getByRole('combobox', { name: '类别' }).fill('停用')
  await expect(form.getByText('没有匹配的启用类别')).toBeVisible()
  await pickCategory(form, '牛排', '牛排')
  await expect(form.locator('.cp-selected')).toContainText('菜品 / 肉类 / 牛排')
  await expect(form.getByRole('radio', { name: '自动标注' })).toBeChecked()

  // Scenes: the rule preview for steak (oven by subtree, us_diet by its own cuisine)
  const sceneRow = (name: string) => form.locator('.ia-scene', { hasText: name })
  await expect(sceneRow('通用').getByRole('checkbox')).toBeDisabled()
  await expect(sceneRow('一体机').getByRole('checkbox')).toBeChecked()
  await expect(sceneRow('一体机')).toContainText('按规则')
  await expect(sceneRow('美国饮食').getByRole('checkbox')).toBeChecked()
  await expect(sceneRow('中国饮食').getByRole('checkbox')).not.toBeChecked()

  // Tags, food annotation, source
  const tags = form.getByLabel('标签', { exact: true })
  await tags.fill('带骨')
  await tags.press('Enter')
  await expect(form.locator('.ia-tag', { hasText: '带骨' })).toBeVisible()
  await form.getByText('食物标注', { exact: true }).click()
  await form.getByLabel('食物状态', { exact: true }).selectOption('cooked')
  const mass = form.getByLabel('总质量', { exact: true })
  await mass.fill('35x')
  await expect(form.getByText('请输入正整数。')).toBeVisible()
  await expect(page.getByRole('button', { name: '开始入库' })).toBeDisabled()
  await expect(page.locator('.mg-import-blockers')).toContainText('批量标注里有需要修改的内容')
  await mass.fill('350')

  // The second image gets its own annotation before the batch scene choice
  await page.getByRole('button', { name: '单独标注', exact: true }).nth(1).click()
  const dialog = page.getByRole('dialog', { name: '单张标注' })
  await expect(dialog.locator('.cp-selected')).toContainText('牛排')  // copied from the batch
  await pickCategory(dialog, '红烧', '红烧肉')
  await expect(dialog.locator('.ia-scene', { hasText: '中国饮食' }).getByRole('checkbox')).toBeChecked()  // dish's cuisine
  await screenshot(page, info, 'import-single-dialog')
  await dialog.getByRole('button', { name: '保存单独标注' }).click()
  await expect(page.locator('.mg-upload-card').nth(1)).toContainText('单独标注')
  await expect(page.locator('.mg-upload-card').nth(1)).toContainText('红烧肉')

  // A choice against the rules for the batch: into 中国饮食 by hand
  await sceneRow('中国饮食').getByRole('checkbox').check()
  await expect(sceneRow('中国饮食')).toContainText('人工纳入')
  await page.setViewportSize({ width: 1440, height: 2000 })  // the whole form at once: the app scrolls inside its shell
  await screenshot(page, info, 'import-annotated')
  await page.setViewportSize({ width: 1440, height: 1000 })
  // The form is long: with its top in view, the run bar still shows 「开始入库」
  await form.getByRole('combobox', { name: '类别' }).scrollIntoViewIfNeeded()
  await expect(page.getByRole('button', { name: '开始入库' })).toBeInViewport()
  await screenshot(page, info, 'import-sticky')

  await page.getByRole('button', { name: '开始入库' }).click()
  await expect(page.locator('.mg-upload-card.mg-job-success')).toHaveCount(2)

  expect(api.indexCalls.map(call => call.filename)).toEqual(['steak.png', 'pork.png'])
  expect(api.indexCalls[0].fields).toEqual({
    category: 'dish.meat.steak', label_status: 'auto', tags: '["带骨"]', source_type: 'user_upload', license: 'unknown',
    food_state: 'cooked', total_mass_g: '350',
  })
  expect(api.indexCalls[1].fields).toMatchObject({ category: 'dish.meat.pork', label_status: 'auto', tags: '["带骨"]' })
  expect(api.indexCalls[1].fields.scenes).toBeUndefined()  // membership follows the rules, not hints
  // Only the batch image needed a write: pork is in 中国饮食 by rule already
  expect(api.membershipCalls).toEqual([{ path: '00000000-0000-4000-8000-000000000001/3', body: { is_member: true } }])
  await expect(page.locator('.mg-upload-card').first().locator('.mg-job-scenes')).toContainText('中国饮食')
  await screenshot(page, info, 'import-done')
})

test('相同图片已在库中：提示沿用原有标注，不写场景调整', async ({ page }) => {
  const api = await mockApi(page, { created: false })
  await openImport(page)
  await page.getByLabel('选择待入库图片').setInputFiles([upload('again.png')])
  const form = batch(page)
  await pickCategory(form, '牛排', '牛排')
  await form.locator('.ia-scene', { hasText: '中国饮食' }).getByRole('checkbox').check()
  await page.getByRole('button', { name: '开始入库' }).click()
  await expect(page.locator('.mg-upload-card.mg-job-success')).toHaveCount(1)
  await expect(page.locator('.mg-job-note')).toContainText('相同图片已在图库中')
  expect(api.membershipCalls).toEqual([])
})

test('窄屏：标注表单排在队列下方，没有横向滚动', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await mockApi(page)
  await openImport(page)
  await page.getByLabel('选择待入库图片').setInputFiles([upload('a.png')])
  await pickCategory(batch(page), '牛排', '牛排')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await screenshot(page, info, 'import-narrow')
})
