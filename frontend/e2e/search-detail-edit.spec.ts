import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectToast } from './notice'

// Fixtures only: every API and image request is answered below; nothing reaches a real backend.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

const category = (id: number, code: string, parent: number | null, name: string) => ({
  id, code, parent_id: parent, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }], cuisines: [],
  heating_group: null, sort_order: 0, is_active: true, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
})
const CATEGORIES = [
  category(1, 'dish', null, '菜品'),
  category(2, 'dish.meat', 1, '肉类'),
  category(3, 'dish.meat.steak', 2, '牛排'),
  category(4, 'dish.meat.pork', 2, '红烧肉'),
]
const ref = (id: number, code: string, name: string) => ({ id, code, name })
// 一体机, searched by default here, labels 肉类 as 烤肉 and, more specifically, 牛排 as 牛排档
const SCENES = [
  { id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
  {
    id: 2, code: 'oven', name: '一体机', description: null, include_all: false, outputs_heating: true, is_active: true, created_at: null, member_count: 0,
    labels: [
      { id: 10, code: 'meat', name: '烤肉', sort_order: 0, is_active: true, rule_count: 1 },
      { id: 11, code: 'steak', name: '牛排档', sort_order: 1, is_active: true, rule_count: 1 },
    ],
    rules: [
      { id: 1, category: ref(2, 'dish.meat', '肉类'), cuisine: null, label: ref(10, 'meat', '烤肉') },
      { id: 2, category: ref(3, 'dish.meat.steak', '牛排'), cuisine: null, label: ref(11, 'steak', '牛排档') },
    ],
  },
]
const enumItem = (name: string, values: Array<[string, string]>) => ({ name, label: name, editable: true, values: values.map(([value, label]) => ({ value, label })) })
const ENUMS = [
  enumItem('food_state', [['raw', '生'], ['cooked', '熟']]),
  enumItem('food_processing', [['sliced', '切片']]),
  enumItem('source_type', [['user_upload', '用户上传']]),
  enumItem('license', [['unknown', '未知']]),
]

type Image = Record<string, unknown> & { id: string; category_id: number | null; tags: string[]; scenes: string[] }

/** A stored steak (GET /api/images/{id}); the first one has the tag 带骨, the others say which they are. */
function storedImage(n: number): Image {
  return {
    id: imageId(n), sha256: 'ab'.repeat(32), original_url: `https://cdn.test/o/${n}.png`, thumb_url: `https://cdn.test/t/${n}.png`,
    width: 640, height: 480, category_id: 3, category: '牛排', category_code: 'dish.meat.steak', label_status: 'auto',
    scenes: ['general', 'oven'], description: '煎牛排', tags: n === 1 ? ['带骨'] : [`第${n}张`], source_type: 'user_upload', license: 'unknown',
    food_state: 'raw', food_processing: null, food_count: null, total_mass_g: null, water_mass_g: null, main_ingredient_mass_g: null,
    heating_group: null, payload_version: 3, created_at: '2026-11-10T08:00:00+00:00', updated_at: null, embeddings: [],
  }
}

/** The image as a search result card (§6.1.1), with the scene label the server gives it. */
const hitOf = (image: Image, n: number) => ({
  tags: image.tags.join('、'), description: image.description, original_url: image.original_url, similarity: 0.91 - n * 0.01,
  details: {
    id: image.id, category_id: image.category_id, category: image.category, scenes: image.scenes, scene_label: '牛排档', label_status: image.label_status,
    images: [{ image_url: image.original_url, thumbnail_url: image.thumb_url, width: 640, height: 480 }],
    food_state: image.food_state, heating_group: image.heating_group,
  },
})

async function mockApi(page: Page, { count = 2 } = {}) {
  const images = new Map(Array.from({ length: count }, (_, index) => [imageId(index + 1), storedImage(index + 1)]))
  const calls = { searches: 0, patches: [] as unknown[], deletes: [] as string[] }
  const vector = (image: Image) => ({
    status: 'synced', collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: true, payload: {}, expected_payload: {},
    payload_matches: true, payload_version: image.payload_version, synced_version: image.payload_version, embedded_at: null,
  })
  // Only the backend: Vite also serves source modules under paths such as /src/shared/api/
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: ENUMS } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'oven' } })
    if (path === '/api/categories') return route.fulfill({ json: { ok: true, categories: CATEGORIES, heating_groups: [], root_codes: ['dish'] } })
    if (path === '/api/search') {
      calls.searches++
      const items = [...images.values()].map((image, index) => hitOf(image, index + 1))
      return route.fulfill({ json: { ok: true, trace_id: 't', source: 'self', scene: 'oven', outputs_heating: true, items } })
    }
    const membership = /^\/api\/images\/([^/]+)\/scene-memberships$/.exec(path)
    if (membership && method === 'GET') {
      const image = images.get(membership[1])!
      return route.fulfill({ json: { ok: true, image_id: image.id, payload_version: image.payload_version, scenes: image.scenes, memberships: [
        { scene_id: 1, scene_code: 'general', is_active: true, include_all: true, is_member: true, origin: 'implicit', manual_override: null },
        { scene_id: 2, scene_code: 'oven', is_active: true, include_all: false, is_member: true, origin: 'rule', manual_override: null },
      ] } })
    }
    const one = /^\/api\/images\/([^/]+)$/.exec(path)
    const image = one ? images.get(one[1]) : undefined
    if (one && !image) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 't' } })
    if (image && method === 'GET') return route.fulfill({ json: { ok: true, image, vector: vector(image) } })
    if (image && method === 'PATCH') {
      const patch = request.postDataJSON() as Record<string, unknown>
      calls.patches.push(patch)
      const next = { ...image, ...patch, payload_version: Number(image.payload_version) + 1 } as Image
      if ('category_id' in patch) {
        const picked = CATEGORIES.find(item => item.id === patch.category_id)
        Object.assign(next, { category: picked?.name ?? null, category_code: picked?.code ?? null })
      }
      images.set(image.id, next)
      return route.fulfill({ json: { ok: true, image: next, vector: vector(next), vector_sync: 'pushed', sync_required: false } })
    }
    if (image && method === 'DELETE') {
      calls.deletes.push(image.id)
      images.delete(image.id)
      return route.fulfill({ json: { ok: true, image_id: image.id, deleted_at: '2026-11-26T09:00:00+00:00', cleanup: 'done' } })
    }
    return route.abort('blockedbyclient')
  })
  await page.route('https://cdn.test/**', route => route.fulfill({ contentType: 'image/png', body: png }))
  return calls
}

async function searchByUrl(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '图片链接', exact: true }).click()
  await page.getByLabel('图片地址').fill('https://cdn.test/query.png')
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
}

const field = (dialog: Locator, name: string) => dialog.locator('.rd-fields > div').filter({ has: dialog.page().locator('dt').getByText(name, { exact: true }) }).locator('dd')

async function pickCategory(scope: Locator, query: string, option: string) {
  await scope.getByRole('combobox', { name: '类别' }).fill(query)
  await scope.getByRole('treeitem', { name: new RegExp(`^${option}`) }).click()
}

test('搜索结果详情里修改标注：保存后结果卡随之更新，场景标签按场景规则重算，不用重新搜索', async ({ page }) => {
  const calls = await mockApi(page)
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(2)
  await expect(cards.first()).toContainText('牛排档')

  await cards.first().getByRole('button', { name: /^查看图片详情/ }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  await expect(dialog.getByRole('heading', { level: 3 })).toHaveText('牛排')
  await expect(dialog.locator('.rd-sub')).toHaveText('菜品 / 肉类 / 牛排')  // where the category sits in the tree
  await expect(dialog.locator('.rd-status')).toHaveText('自动标注')
  await expect(field(dialog, '场景标签')).toHaveText('牛排档')
  await expect(field(dialog, '加热组')).toHaveText('—')
  await expect(field(dialog, '食物状态')).toHaveText('生')
  await expect(field(dialog, '所属场景')).toHaveText('通用一体机')

  // The gallery's editor, in the same dialog
  await dialog.getByRole('button', { name: '编辑标注', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑标注' })
  await pickCategory(editor, '红烧', '红烧肉')
  await expect(editor.getByRole('radio', { name: '已核验' })).toBeChecked()
  const tags = editor.getByLabel('标签', { exact: true })
  await tags.fill('红烧')
  await tags.press('Enter')
  // Unsaved edits keep the dialog open, and on this image
  await expect(editor.getByRole('button', { name: '关闭弹窗' })).toBeDisabled()
  await expect(editor.getByRole('button', { name: '返回详情' })).toBeDisabled()
  await editor.getByRole('button', { name: '保存修改' }).click()
  await expectToast(page, '已保存，向量库已同步，检索已生效。', 'success')
  expect(calls.patches).toEqual([{ category_id: 4, label_status: 'verified', tags: ['带骨', '红烧'] }])

  // Back in the detail: what is saved, the scene label of the new category (一体机's rule on 肉类), the search's score
  await editor.getByRole('button', { name: '返回详情' }).click()
  await expect(dialog.getByRole('heading', { level: 3 })).toHaveText('红烧肉')
  await expect(dialog.locator('.rd-sub')).toHaveText('菜品 / 肉类 / 红烧肉')
  await expect(dialog.locator('.rd-status')).toHaveText('已核验')
  await expect(dialog.locator('.rd-head .result-edited')).toHaveText('已修改')
  await expect(field(dialog, '场景标签')).toHaveText('烤肉')
  await expect(field(dialog, '标签')).toHaveText('带骨、红烧')
  await expect(dialog.locator('.rd-score > b')).toHaveText('90.0%')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  // The card says the same; the results filter knows the new category; nothing was searched again
  await expect(cards.first()).toContainText('红烧肉')
  await expect(cards.first()).toContainText('烤肉')
  await expect(cards.first().locator('.result-edited')).toHaveText('已修改')
  await expect(cards.nth(1).locator('.result-edited')).toHaveCount(0)
  await page.getByLabel('筛选类别').selectOption('红烧肉')
  await expect(cards).toHaveCount(1)
  await page.getByRole('button', { name: '列表展示结果', exact: true }).click()
  await expect(page.locator('.result-table tbody tr').first().locator('.result-edited')).toHaveText('已修改')
  expect(calls.searches).toBe(1)
})

test('搜索结果详情里删除图片：确认后结果和勾选里都去掉这张', async ({ page }) => {
  const calls = await mockApi(page)
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(2)
  await cards.first().getByRole('checkbox').check()
  await expect(page.locator('.selection-count')).toHaveText('已选择 1 张')

  await cards.first().getByRole('button', { name: /^查看图片详情/ }).click()
  await page.getByRole('dialog', { name: '图片详情' }).getByRole('button', { name: '编辑标注', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑标注' })
  await editor.getByRole('button', { name: '删除图片' }).click()
  await editor.getByRole('button', { name: '确认删除' }).click()
  await expect(editor).toBeHidden()
  await expectToast(page, '已删除图片，向量和原图、缩略图已清理。', 'success')
  expect(calls.deletes).toEqual([imageId(1)])
  await expect(cards).toHaveCount(1)
  await expect(page.locator('.selection-count')).toHaveText('已选择 0 张')
  await expect(page.locator('.results-footer')).toContainText('共 1 张图片')
})

test('详情里用 ← / → 或图片两侧的按钮切换结果，结果页跟着翻；编辑时方向键不切换', async ({ page }) => {
  await mockApi(page, { count: 13 })
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(12)
  await cards.nth(11).getByRole('button', { name: /^查看图片详情/ }).click()
  const dialog = page.getByRole('dialog', { name: '图片详情' })
  const position = dialog.locator('.rd-position')
  await expect(position).toHaveText('12 / 13')
  await expect(field(dialog, '标签')).toHaveText('第12张')

  await page.keyboard.press('ArrowRight')
  await expect(position).toHaveText('13 / 13')
  await expect(field(dialog, '标签')).toHaveText('第13张')
  await expect(dialog.getByRole('button', { name: '下一张' })).toHaveCount(0)
  await expect(page.locator('.pagination > span')).toHaveText('2 / 2')  // the results page follows

  await dialog.getByRole('button', { name: '上一张' }).click()
  await expect(position).toHaveText('12 / 13')
  await expect(page.locator('.pagination > span')).toHaveText('1 / 2')

  // While editing, the arrows belong to the editor
  await dialog.getByRole('button', { name: '编辑标注', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑标注' })
  await expect(editor.getByRole('combobox', { name: '类别' })).toBeVisible()
  await page.keyboard.press('ArrowRight')
  await editor.getByRole('button', { name: '返回详情' }).click()
  await expect(position).toHaveText('12 / 13')
})
