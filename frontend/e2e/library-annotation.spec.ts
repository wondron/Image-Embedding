import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const STEAK_IMAGE = '00000000-0000-4000-8000-00000000000a'
const PLAIN_IMAGE = '00000000-0000-4000-8000-00000000000b'

const category = (id: number, code: string, parent: number | null, name: string, isActive = true) => ({
  id, code, parent_id: parent, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }], cuisines: [],
  heating_group: null, sort_order: 0, is_active: isActive, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
})
const CATEGORIES = [
  category(1, 'dish', null, '菜品'),
  category(2, 'dish.meat', 1, '肉类'),
  category(3, 'dish.meat.steak', 2, '牛排'),
  category(4, 'dish.meat.pork', 2, '红烧肉'),
  category(5, 'dish.retired', 1, '停用类别', false),
]
const SCENES = [
  { id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
  { id: 2, code: 'oven', name: '一体机', description: null, include_all: false, outputs_heating: true, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
]
const enumItem = (name: string, values: Array<[string, string]>) => ({ name, label: name, editable: true, values: values.map(([value, label]) => ({ value, label })) })
const ENUMS = [
  enumItem('food_state', [['raw', '生'], ['cooked', '熟']]),
  enumItem('food_processing', [['sliced', '切片']]),
  enumItem('source_type', [['user_upload', '用户上传']]),
  enumItem('license', [['unknown', '未知']]),
]

type Image = Record<string, unknown>

function steakImage(): Image {
  return {
    id: STEAK_IMAGE, sha256: 'ab'.repeat(32), original_url: 'https://cdn.test/o/steak.png', thumb_url: 'https://cdn.test/t/steak.png',
    width: 640, height: 480, category_id: 3, category: '牛排', category_code: 'dish.meat.steak', label_status: 'auto',
    scenes: ['general', 'oven'], description: '煎牛排', tags: ['带骨'], source_type: 'user_upload', license: 'unknown',
    food_state: 'raw', food_processing: null, food_count: null, total_mass_g: null, water_mass_g: null, main_ingredient_mass_g: null,
    heating_group: null, payload_version: 3, created_at: '2026-11-10T08:00:00+00:00', updated_at: null,
    embeddings: [{ model_id: 1, name: 'dino', version: 'v2', collection: 'c_dino_v2', model_status: 'current', synced_version: 2, embedded_at: null }],
  }
}

const listItem = (image: Image) => ({
  id: image.id, original_url: image.original_url, thumb_url: image.thumb_url, category_id: image.category_id, category: image.category,
  scenes: image.scenes, label_status: image.label_status, description: image.description, tags: image.tags,
  width: 640, height: 480, created_at: image.created_at, updated_at: image.updated_at,
})

async function mockApi(page: Page) {
  const state = { image: steakImage(), vector: 'stale' as 'stale' | 'synced', deleted: false }
  const calls = { list: [] as Array<Record<string, string>>, patches: [] as unknown[], pushes: 0, deletes: [] as string[] }
  const plain = { ...steakImage(), id: PLAIN_IMAGE, category_id: null, category: null, label_status: 'unlabeled', description: '', tags: [], scenes: ['general'] }
  // Only the backend: Vite also serves source modules under paths such as /src/shared/api/
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
      const items = state.deleted ? [listItem(plain)] : [listItem(state.image), listItem(plain)]
      return route.fulfill({ json: { ok: true, trace_id: 't', scene: 'general', page: 1, page_size: 24, total: items.length, items } })
    }
    if (path.endsWith('/scene-memberships') && method === 'GET') {
      return route.fulfill({ json: { ok: true, image_id: STEAK_IMAGE, payload_version: state.image.payload_version, scenes: state.image.scenes, memberships: [
        { scene_id: 1, scene_code: 'general', is_active: true, include_all: true, is_member: true, origin: 'implicit', manual_override: null },
        { scene_id: 2, scene_code: 'oven', is_active: true, include_all: false, is_member: true, origin: 'rule', manual_override: null },
      ] } })
    }
    if (path === `/api/images/${STEAK_IMAGE}/vector-sync` && method === 'POST') {
      calls.pushes++
      state.vector = 'synced'
      return route.fulfill({ json: { ok: true, image_id: STEAK_IMAGE, payload_version: state.image.payload_version, vector_sync: 'pushed', sync_required: false } })
    }
    if (path === `/api/images/${STEAK_IMAGE}`) {
      if (state.deleted) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 't' } })
      if (method === 'GET') {
        const synced = state.vector === 'synced'
        return route.fulfill({ json: { ok: true, image: state.image, vector: {
          status: state.vector, collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: true,
          payload: { category_id: 3, scenes: synced ? state.image.scenes : ['general'], food_state: state.image.food_state, label_status: state.image.label_status },
          expected_payload: { category_id: state.image.category_id, scenes: state.image.scenes, food_state: state.image.food_state, label_status: state.image.label_status },
          payload_matches: synced, payload_version: state.image.payload_version, synced_version: synced ? state.image.payload_version : 2, embedded_at: null,
        } } })
      }
      if (method === 'PATCH') {
        const patch = request.postDataJSON() as Record<string, unknown>
        calls.patches.push(patch)
        const next = { ...state.image, ...patch, payload_version: Number(state.image.payload_version) + 1 }
        if ('category_id' in patch) {
          const picked = CATEGORIES.find(item => item.id === patch.category_id)
          Object.assign(next, { category: picked?.name ?? null, category_code: picked?.code ?? null })
        }
        state.image = next
        state.vector = 'synced'
        return route.fulfill({ json: { ok: true, image: next, vector_sync: 'pushed', sync_required: false } })
      }
      if (method === 'DELETE') {
        calls.deletes.push(STEAK_IMAGE)
        state.deleted = true
        return route.fulfill({ json: { ok: true, image_id: STEAK_IMAGE, deleted_at: '2026-11-11T09:00:00+00:00', cleanup: 'done' } })
      }
    }
    return route.abort('blockedbyclient')
  })
  await page.route('https://cdn.test/**', route => route.fulfill({ contentType: 'image/png', body: png }))
  return calls
}

async function openLibrary(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^图片数据/ }).click()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

async function pickCategory(scope: Locator, query: string, option: string) {
  await scope.getByRole('combobox', { name: '类别' }).fill(query)
  await scope.getByRole('option', { name: new RegExp(`^${option}`) }).click()
}

test('图库：类别与标注状态筛选取自数据库；单张图片可查看向量状态、重推、编辑和删除', async ({ page }, info) => {
  const calls = await mockApi(page)
  await openLibrary(page)
  await expect(page.locator('.mg-library-card')).toHaveCount(2)
  expect(calls.list[0]).toEqual({ page: '1', page_size: '24', scene: 'general' })
  const steakCard = page.locator('.mg-library-card').first()
  await expect(steakCard).toContainText('煎牛排')
  await expect(steakCard).toContainText('自动标注')
  await expect(steakCard).toContainText('#带骨')
  await expect(page.locator('.mg-library-card').nth(1)).toContainText('未标注')

  // Category filter: active categories from GET /api/categories; a category means its subtree
  await page.getByRole('button', { name: '类别筛选：全部类别' }).click()
  const popover = page.getByRole('dialog', { name: '按类别筛选' })
  await popover.getByRole('combobox').fill('停用')
  await expect(popover.getByText('没有匹配的启用类别')).toBeVisible()
  await popover.getByRole('combobox').fill('肉')
  await screenshot(page, info, 'library-filter')
  await popover.getByRole('option', { name: /^肉类/ }).click()
  await expect(page.getByRole('button', { name: '类别筛选：肉类' })).toBeVisible()
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('2')
  await page.getByLabel('标注状态筛选').selectOption('auto')
  await expect.poll(() => calls.list.at(-1)).toMatchObject({ category_id: '2', label_status: 'auto' })

  // The editor: what is stored, the vector state, the re-push
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  await expect(dialog.locator('.ie-vector')).toContainText('待同步')
  await expect(dialog.locator('.ie-diff')).toContainText('场景')
  await expect(dialog.locator('.ie-meta')).toContainText('菜品 / 肉类 / 牛排')
  await screenshot(page, info, 'library-editor')
  await dialog.getByRole('button', { name: '重推 payload' }).click()
  await expect(dialog.getByText('已重推，向量库和数据库一致了。')).toBeVisible()
  await expect(dialog.locator('.ie-vector')).toContainText('已同步')
  expect(calls.pushes).toBe(1)

  // Edit: a category picked by hand becomes verified; only what changed is sent
  await pickCategory(dialog, '红烧', '红烧肉')
  await expect(dialog.getByRole('radio', { name: '已核验' })).toBeChecked()
  const tags = dialog.getByLabel('标签', { exact: true })
  await tags.fill('红烧')
  await tags.press('Enter')
  const foodState = dialog.getByLabel('食物状态', { exact: true })
  await expect(foodState).toBeVisible()  // the image has a food annotation: the section opens on its own
  await expect(foodState).toHaveValue('raw')
  await foodState.selectOption('cooked')
  await dialog.getByLabel('总质量', { exact: true }).fill('300')
  await expect(dialog.locator('.ie-changes')).toContainText('未保存：类别、标注状态、标签、食物状态、总质量')
  await expect(dialog.getByRole('button', { name: '关闭弹窗' })).toBeDisabled()  // unsaved edits keep it open
  await screenshot(page, info, 'library-editor-dirty')
  const listsBefore = calls.list.length
  await dialog.getByRole('button', { name: '保存修改' }).click()
  await expect(dialog.getByText('已保存，检索已生效。')).toBeVisible()
  expect(calls.patches).toEqual([{ category_id: 4, label_status: 'verified', tags: ['带骨', '红烧'], food_state: 'cooked', total_mass_g: 300 }])
  await expect(dialog.locator('.ie-meta')).toContainText('菜品 / 肉类 / 红烧肉')
  await expect(dialog.getByRole('button', { name: '关闭弹窗' })).toBeEnabled()
  await expect.poll(() => calls.list.length).toBeGreaterThan(listsBefore)  // the library page reloads

  // Delete, after a confirmation
  await dialog.getByRole('button', { name: '删除图片' }).click()
  await dialog.getByRole('button', { name: '确认删除' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByText('已删除图片，向量和原图、缩略图已清理。')).toBeVisible()
  expect(calls.deletes).toEqual([STEAK_IMAGE])
  await expect(page.locator('.mg-library-card')).toHaveCount(1)
})

test('图库：撤销修改后可以关闭，没有改动的保存按钮不可用', async ({ page }) => {
  await mockApi(page)
  await openLibrary(page)
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  const save = dialog.getByRole('button', { name: '保存修改' })
  await expect(save).toBeDisabled()
  await dialog.getByLabel('描述', { exact: true }).fill('改过的描述')
  await expect(save).toBeEnabled()
  await expect(dialog.getByRole('button', { name: '关闭弹窗' })).toBeDisabled()
  await dialog.getByRole('button', { name: '撤销修改' }).click()
  await expect(save).toBeDisabled()
  await dialog.getByRole('button', { name: '关闭弹窗' }).click()
  await expect(dialog).toBeHidden()
})

test('窄屏：筛选弹层和编辑弹窗不产生横向滚动', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await mockApi(page)
  await openLibrary(page)
  await page.getByRole('button', { name: '类别筛选：全部类别' }).click()
  await expect(page.getByRole('dialog', { name: '按类别筛选' })).toBeVisible()
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  await expect(page.getByRole('dialog', { name: '图片标注' }).locator('.ie-vector')).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
  await screenshot(page, info, 'library-editor-narrow')
})
