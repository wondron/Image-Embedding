import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'
import { acknowledgeNotice, expectToast } from './notice'

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

const listItem = (image: Image, vectorStatus: string) => ({
  id: image.id, original_url: image.original_url, thumb_url: image.thumb_url, category_id: image.category_id, category: image.category,
  scenes: image.scenes, label_status: image.label_status, description: image.description, tags: image.tags,
  width: 640, height: 480, created_at: image.created_at, updated_at: image.updated_at, vector_status: vectorStatus,
})

/**
 * legacyWrites: answer PATCH / vector-sync like a server from before writes carried `vector`.
 * vector: the steak image's state in the vector store; reembed: how POST .../reembed answers.
 * categories: the category tree; listTotal: the total the list reports; fillPage: answer every
 * list request with a full page of plain images.
 */
async function mockApi(page: Page, {
  legacyWrites = false, vector = 'stale' as 'stale' | 'missing', reembed = 'ok' as 'ok' | 'unreadable',
  categories = CATEGORIES as unknown[], listTotal = null as number | null, fillPage = false,
} = {}) {
  const state = { image: steakImage(), vector: vector as 'stale' | 'synced' | 'missing', deleted: false }
  const calls = { list: [] as Array<Record<string, string>>, patches: [] as unknown[], pushes: 0, reembeds: 0, deletes: [] as string[], details: 0, categoryReads: 0 }
  // The vector state as the server reports it: before a push the point still has the old scenes
  const vectorWire = () => {
    const expected_payload = { category_id: state.image.category_id, scenes: state.image.scenes, food_state: state.image.food_state, label_status: state.image.label_status }
    if (state.vector === 'missing') {
      return { status: 'missing', collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: false, payload: null, expected_payload,
        payload_matches: null, payload_version: state.image.payload_version, synced_version: 2, embedded_at: null }
    }
    const synced = state.vector === 'synced'
    return {
      status: state.vector, collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: true,
      payload: { category_id: synced ? state.image.category_id : 3, scenes: synced ? state.image.scenes : ['general'], food_state: state.image.food_state, label_status: state.image.label_status },
      expected_payload,
      payload_matches: synced, payload_version: state.image.payload_version, synced_version: synced ? state.image.payload_version : 2, embedded_at: null,
    }
  }
  const afterWrite = () => (legacyWrites ? {} : { vector: vectorWire() })
  const plain = { ...steakImage(), id: PLAIN_IMAGE, category_id: null, category: null, label_status: 'unlabeled', description: '', tags: [], scenes: ['general'] }
  // Only the backend: Vite also serves source modules under paths such as /src/shared/api/
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: ENUMS } })
    if (path === '/api/categories') {
      calls.categoryReads++
      return route.fulfill({ json: { ok: true, categories, heating_groups: [], root_codes: ['dish'] } })
    }
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
    if (path === '/api/images' && method === 'GET') {
      calls.list.push(Object.fromEntries(url.searchParams))
      const pageSize = Number(url.searchParams.get('page_size'))
      const items = fillPage
        ? Array.from({ length: pageSize }, (_, index) => listItem({ ...plain, id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`, description: `图片${index + 1}` }, 'synced'))
        : state.deleted ? [listItem(plain, 'synced')] : [listItem(state.image, state.vector), listItem(plain, 'synced')]
      return route.fulfill({ json: { ok: true, trace_id: 't', scene: 'general', page: Number(url.searchParams.get('page')), page_size: pageSize, total: listTotal ?? items.length, items } })
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
      return route.fulfill({ json: { ok: true, image_id: STEAK_IMAGE, payload_version: state.image.payload_version, ...afterWrite(), vector_sync: 'pushed', sync_required: false } })
    }
    if (path === `/api/images/${STEAK_IMAGE}/reembed` && method === 'POST') {
      calls.reembeds++
      if (reembed === 'unreadable') {
        return route.fulfill({ status: 502, json: { ok: false, error: 'IMAGE_FILE_UNAVAILABLE', detail: 'stored original could not be read', trace_id: 't' } })
      }
      state.vector = 'synced'
      return route.fulfill({ json: { ok: true, image_id: STEAK_IMAGE, payload_version: state.image.payload_version, vector: vectorWire() } })
    }
    if (path === `/api/images/${STEAK_IMAGE}`) {
      if (state.deleted) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 't' } })
      if (method === 'GET') {
        calls.details++
        return route.fulfill({ json: { ok: true, image: state.image, vector: vectorWire() } })
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
        return route.fulfill({ json: { ok: true, image: next, ...afterWrite(), vector_sync: 'pushed', sync_required: false } })
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
  await scope.getByRole('treeitem', { name: new RegExp(`^${option}`) }).click()
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

  // Category filter: the tree from GET /api/categories, disabled ones left out; a category means its subtree
  const filters = page.getByRole('complementary', { name: '图片筛选' })
  const categorySearch = filters.getByRole('searchbox', { name: '搜索类别' })
  await categorySearch.fill('停用')
  await expect(filters.getByText('没有匹配的类别')).toBeVisible()
  await expect(filters).toContainText('另有 1 个被上面的选项隐藏')
  await categorySearch.fill('肉')
  await screenshot(page, info, 'library-filter')
  await filters.getByRole('tree', { name: '类别' }).getByRole('treeitem', { name: /^肉类/ }).click()
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('2')
  await expect(page.getByRole('group', { name: '生效的筛选' })).toContainText('菜品 / 肉类')
  await filters.getByRole('radiogroup', { name: '标注状态' }).getByRole('radio', { name: '自动标注' }).check()
  await expect.poll(() => calls.list.at(-1)).toMatchObject({ category_id: '2', label_status: 'auto' })

  // The card says the image lags the vector store; the footer counts the page
  await expect(steakCard.locator('.mg-vector-flag')).toHaveText('待同步')
  await expect(page.locator('.mg-pagination')).toContainText('本页 1 张待同步')

  // The editor: what is stored, the vector state in words, the push
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  await expect(dialog.locator('.ie-vector')).toContainText('待同步')
  // Scene names, not codes: the point still has only 通用, the database says 通用 and 一体机
  await expect(dialog.locator('.ie-diff tbody tr')).toHaveText([/所属场景\s*通用\s*通用、一体机/])
  await expect(dialog.locator('.ie-meta')).toContainText('菜品 / 肉类 / 牛排')
  await screenshot(page, info, 'library-editor')
  const detailReads = calls.details
  await dialog.getByRole('button', { name: '按数据库同步到向量库' }).click()
  await expectToast(page, '已同步，向量库和数据库一致了。', 'success')
  await expect(dialog.locator('.ie-vector')).toContainText('已同步')
  expect(calls.pushes).toBe(1)
  expect(calls.details).toBe(detailReads)  // the push answered the new state itself
  await expect(steakCard.locator('.mg-vector-flag')).toHaveCount(0)  // the gallery reloaded

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
  const readsBeforeSave = calls.details
  await dialog.getByRole('button', { name: '保存修改' }).click()
  await expectToast(page, '已保存，向量库已同步，检索已生效。', 'success')
  expect(calls.patches).toEqual([{ category_id: 4, label_status: 'verified', tags: ['带骨', '红烧'], food_state: 'cooked', total_mass_g: 300 }])
  await expect(dialog.locator('.ie-vector')).toContainText('已同步')
  expect(calls.details).toBe(readsBeforeSave)  // the PATCH answered the vector state
  await expect(dialog.locator('.ie-meta')).toContainText('菜品 / 肉类 / 红烧肉')
  await expect(dialog.getByRole('button', { name: '关闭弹窗' })).toBeEnabled()
  await expect.poll(() => calls.list.length).toBeGreaterThan(listsBefore)  // the library page reloads

  // Delete, after a confirmation
  await dialog.getByRole('button', { name: '删除图片' }).click()
  await dialog.getByRole('button', { name: '确认删除' }).click()
  await expect(dialog).toBeHidden()
  await expectToast(page, '已删除图片，向量和原图、缩略图已清理。', 'success')
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

test('图库：旧版服务端的写入不带向量状态时，再读一次详情', async ({ page }) => {
  const calls = await mockApi(page, { legacyWrites: true })
  await openLibrary(page)
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  await expect(dialog.locator('.ie-vector')).toContainText('待同步')
  const reads = calls.details
  await dialog.getByRole('button', { name: '按数据库同步到向量库' }).click()
  await expect(dialog.locator('.ie-vector')).toContainText('已同步')
  expect(calls.details).toBe(reads + 1)
})

test('图库：没有向量的图片用存储里的原图重新生成，不用重新上传', async ({ page }) => {
  const calls = await mockApi(page, { vector: 'missing' })
  await openLibrary(page)
  const steakCard = page.locator('.mg-library-card').first()
  await expect(steakCard.locator('.mg-vector-flag')).toHaveText('没有向量')
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  const vector = dialog.locator('.ie-vector')
  await expect(vector).toContainText('没有向量')
  await expect(vector.getByRole('button', { name: /或去「上传与导入」重新上传/ })).toBeVisible()  // the fallback stays offered
  const reads = calls.details
  await vector.getByRole('button', { name: '用存储里的原图重新生成向量' }).click()
  await expectToast(page, '已用原图重新生成向量，检索可以查到这张图了。', 'success')
  await expect(vector).toContainText('已同步')
  expect(calls.reembeds).toBe(1)
  expect(calls.details).toBe(reads)  // the answer carried the new state
  await expect(steakCard.locator('.mg-vector-flag')).toHaveCount(0)
})

test('图库：原图读不到时说明只能重新上传', async ({ page }) => {
  await mockApi(page, { vector: 'missing', reembed: 'unreadable' })
  await openLibrary(page)
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  await dialog.locator('.ie-vector').getByRole('button', { name: '用存储里的原图重新生成向量' }).click()
  await expect(page.getByRole('alertdialog', { name: '重新生成向量失败' })).toBeVisible()  // stays until it is read
  await acknowledgeNotice(page, '读不到存储里的原图', 'error')
  await expect(dialog.locator('.ie-vector')).toContainText('没有向量')
})

test('图库：可以只看待同步或没有向量的图片', async ({ page }) => {
  const calls = await mockApi(page)
  await openLibrary(page)
  await expect(page.locator('.mg-library-card')).toHaveCount(2)
  const vector = page.getByRole('radiogroup', { name: '向量库状态' })
  await vector.getByRole('radio', { name: '待同步或没有向量' }).check()
  await expect.poll(() => calls.list.at(-1)?.out_of_sync).toBe('true')
  await vector.getByRole('radio', { name: '全部' }).check()
  await expect.poll(() => calls.list.at(-1)).not.toHaveProperty('out_of_sync')
})

test('图库：点卡片上的类别、标签直接筛选；「未标注」和类别互斥；生效的筛选可以逐个清除', async ({ page }, info) => {
  const calls = await mockApi(page)
  await openLibrary(page)
  const steakCard = page.locator('.mg-library-card').first()
  const filters = page.getByRole('complementary', { name: '图片筛选' })
  const active = page.getByRole('group', { name: '生效的筛选' })

  await steakCard.getByRole('button', { name: '按类别筛选：牛排' }).click()
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('3')
  await expect(active).toContainText('菜品 / 肉类 / 牛排')
  // The tree opens down to the category picked elsewhere and marks it
  await expect(filters.getByRole('treeitem', { name: /^牛排/ })).toHaveAttribute('aria-selected', 'true')
  await expect(steakCard.getByRole('button', { name: '按类别筛选：牛排' })).toHaveCount(0)  // the current one is not offered again

  await steakCard.getByRole('button', { name: '按标签筛选：带骨' }).click()
  await expect.poll(() => calls.list.at(-1)).toMatchObject({ category_id: '3', tag: '带骨' })
  await screenshot(page, info, 'library-active-filters')

  // 「未标注」 has no category: picking it drops the category, and says so
  await filters.getByRole('radiogroup', { name: '标注状态' }).getByRole('radio', { name: '未标注' }).check()
  await expect.poll(() => calls.list.at(-1)).toMatchObject({ label_status: 'unlabeled', tag: '带骨' })
  expect(calls.list.at(-1)).not.toHaveProperty('category_id')
  await expectToast(page, '已清除类别筛选：「未标注」的图片没有类别。', 'info')

  await active.getByRole('button', { name: '清除标签筛选' }).click()
  await expect.poll(() => calls.list.at(-1)).not.toHaveProperty('tag')
  // A typed tag: the 「#」 copied from a card is not part of it
  const tag = filters.getByLabel('标签', { exact: true })
  await tag.fill('#红烧')
  await tag.press('Enter')
  await expect.poll(() => calls.list.at(-1)?.tag).toBe('红烧')
  await expect(filters.getByRole('button', { name: '清除标签筛选：红烧' })).toBeVisible()

  await filters.getByRole('searchbox', { name: '关键词' }).fill('牛')
  await expect.poll(() => calls.list.at(-1)?.q).toBe('牛')
  await active.getByRole('button', { name: '清空筛选' }).click()
  await expect.poll(() => calls.list.at(-1)).toEqual({ page: '1', page_size: '24', scene: 'general' })
  await expect(active).toHaveCount(0)
})

test('图库：上千个类别时树只渲染看得见的行，搜索直接找到深层类别，键盘可以浏览', async ({ page }, info) => {
  const many = [category(1, 'dish', null, '菜品')]
  for (let group = 0; group < 40; group += 1) {
    many.push(category(100 + group, `dish.g${group}`, 1, `大类${group}`))
    for (let leaf = 0; leaf < 50; leaf += 1) many.push({ ...category(10_000 + group * 100 + leaf, `dish.g${group}.c${leaf}`, 100 + group, `菜${group}-${leaf}`), image_count: leaf % 4 })
  }
  const calls = await mockApi(page, { categories: many })
  await openLibrary(page)
  const filters = page.getByRole('complementary', { name: '图片筛选' })
  const tree = filters.getByRole('tree', { name: '类别' })
  await expect(filters).toContainText('2041 个启用')
  // Counts add up the subtree: 40 groups × (0+1+…+49 taken mod 4 = 73)
  await expect(tree.getByRole('treeitem', { name: '菜品，2920 张' })).toBeVisible()

  // A category without images finds nothing: with images in the library, those are hidden by default
  await expect(filters.getByRole('checkbox', { name: /隐藏没有图片的/ })).toBeChecked()
  const search = filters.getByRole('searchbox', { name: '搜索类别' })
  await search.fill('菜3')
  // 11 groups × 50 match; 13 per group have no image (leaf % 4 === 0)
  await expect(filters).toContainText('找到 407 个，另有 143 个被上面的选项隐藏')
  expect(await tree.getByRole('treeitem').count()).toBeLessThan(80)  // a virtual list, not 600 rows
  await search.fill('菜37-42')
  await tree.getByRole('treeitem', { name: /^菜37-42，/ }).click()
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('13742')
  await expect(filters.locator('.lf-current')).toContainText('菜品 / 大类37')

  // Back to the whole tree: the selection is shown in place, the keyboard moves and picks
  await search.fill('')
  await expect(tree.getByRole('treeitem', { name: /^菜37-42，/ })).toBeInViewport()
  await tree.focus()
  await page.keyboard.press('Home')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('100')
  await expect(tree.getByRole('treeitem', { name: /^大类0，/ })).toHaveAttribute('aria-expanded', 'true')
  await screenshot(page, info, 'library-many-categories')

  // What has no image stays out: 菜x-0 (image_count 0) is not in the search
  await search.fill('菜5-0')
  await expect(filters).toContainText('找到 0 个，另有 1 个被上面的选项隐藏')
  await filters.getByRole('button', { name: '全部显示' }).click()
  await expect(tree.getByRole('treeitem', { name: /^菜5-0，/ })).toBeVisible()
})

test('图库：每页数量、跳页与展示方式；展示偏好在浏览器里记住', async ({ page }) => {
  const calls = await mockApi(page, { listTotal: 200, fillPage: true })
  await openLibrary(page)
  await expect(page.locator('.mg-library-card')).toHaveCount(24)
  const pager = page.getByRole('navigation', { name: '翻页' })
  await expect(page.locator('.mg-pagination')).toContainText('共 200 张 · 第 1–24 张')
  await pager.getByRole('button', { name: '最后一页' }).click()
  await expect.poll(() => calls.list.at(-1)?.page).toBe('9')
  await pager.getByRole('spinbutton', { name: '页码' }).fill('3')
  await pager.getByRole('spinbutton', { name: '页码' }).press('Enter')
  await expect.poll(() => calls.list.at(-1)?.page).toBe('3')
  await expect(page.locator('.mg-pagination')).toContainText('第 49–72 张')

  await page.getByLabel('每页数量').selectOption('48')
  await expect.poll(() => calls.list.at(-1)).toMatchObject({ page: '1', page_size: '48' })
  await expect(page.locator('.mg-library-card')).toHaveCount(48)
  await page.getByRole('button', { name: '列表展示' }).click()
  await expect(page.locator('.mg-library-row')).toHaveCount(48)

  await openLibrary(page)  // a new visit
  await expect.poll(() => calls.list.at(-1)?.page_size).toBe('48')
  await expect(page.locator('.mg-library-row')).toHaveCount(48)
  await expect(page.getByRole('button', { name: '列表展示' })).toHaveAttribute('aria-pressed', 'true')
})

test('桌面：左侧筛选窄、右侧图片宽，图片区单独滚动，页头和翻页留在视口里', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockApi(page, { listTotal: 240, fillPage: true })
  await openLibrary(page)
  await expect(page.locator('.mg-library-card')).toHaveCount(24)
  const tools = page.getByRole('complementary', { name: '图片筛选' })
  const images = page.getByRole('region', { name: '图片', exact: true })
  const toolBox = (await tools.boundingBox())!
  const imageBox = (await images.boundingBox())!
  expect(toolBox.width).toBeLessThan(imageBox.width / 2)
  expect(toolBox.x + toolBox.width).toBeLessThanOrEqual(imageBox.x)
  expect(Math.abs(toolBox.y - imageBox.y)).toBeLessThanOrEqual(1)
  // The category tree takes the rest of the column, which fits the screen without scrolling
  expect((await tools.getByRole('tree', { name: '类别' }).boundingBox())!.height).toBeGreaterThanOrEqual(320)
  expect(await tools.evaluate(element => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1)

  const content = page.locator('.lib-content')
  const head = page.locator('.lib-head')
  const pager = page.locator('.lib-pagination')
  const before = { head: await head.boundingBox(), pager: await pager.boundingBox(), tools: await tools.boundingBox() }
  await content.hover()
  await page.mouse.wheel(0, 1500)
  await expect.poll(() => content.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  expect(await head.boundingBox()).toEqual(before.head)
  expect(await pager.boundingBox()).toEqual(before.pager)
  expect(await tools.boundingBox()).toEqual(before.tools)
  await expect(pager).toBeInViewport()
  expect(await page.evaluate(() => ({ document: document.documentElement.scrollTop, body: document.body.scrollTop }))).toEqual({ document: 0, body: 0 })
  await screenshot(page, info, 'library-desktop')

  // Another page starts at the top of the image area
  await page.getByRole('button', { name: '下一页' }).click()
  await expect.poll(() => content.evaluate(element => element.scrollTop)).toBe(0)
})

test('窄屏：筛选收起时在卡片上点类别，展开后树停在这个类别上', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  // 牛排 sits below 40 other groups, so the tree has to scroll to show it
  const many = [category(1, 'dish', null, '菜品')]
  for (let group = 0; group < 40; group += 1) {
    many.push(category(100 + group, `dish.g${group}`, 1, `大类${group}`))
    for (let leaf = 0; leaf < 50; leaf += 1) many.push(category(10_000 + group * 100 + leaf, `dish.g${group}.c${leaf}`, 100 + group, `菜${group}-${leaf}`))
  }
  many.push(category(2, 'dish.meat', 1, '肉类'), category(3, 'dish.meat.steak', 2, '牛排'), category(4, 'dish.meat.pork', 2, '红烧肉'))
  const calls = await mockApi(page, { categories: many })
  await openLibrary(page)
  await page.locator('.mg-library-card').first().getByRole('button', { name: '按类别筛选：牛排' }).click()
  await expect.poll(() => calls.list.at(-1)?.category_id).toBe('3')
  const filters = page.getByRole('complementary', { name: '图片筛选' })
  await filters.getByRole('button', { name: '展开筛选' }).click()
  const steak = filters.getByRole('tree', { name: '类别' }).getByRole('treeitem', { name: /^牛排/ })
  await expect(steak).toHaveAttribute('aria-selected', 'true')
  await expect(steak).toBeInViewport()
})

test('窄屏：筛选收在页头里，展开后和编辑弹窗都不产生横向滚动', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await mockApi(page)
  await openLibrary(page)
  const filters = page.getByRole('complementary', { name: '图片筛选' })
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  await expect(filters.getByRole('searchbox', { name: '搜索类别' })).toBeHidden()
  await expect(page.locator('.mg-library-card')).toHaveCount(2)
  expect(await overflow()).toBeLessThanOrEqual(0)
  await screenshot(page, info, 'library-narrow')
  await filters.getByRole('button', { name: '展开筛选' }).click()
  await expect(filters.getByRole('tree', { name: '类别' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
  await screenshot(page, info, 'library-narrow-filters')
  await filters.getByRole('button', { name: '收起筛选' }).click()
  await page.getByRole('button', { name: '标注 煎牛排' }).click()
  await expect(page.getByRole('dialog', { name: '图片标注' }).locator('.ie-vector')).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
  await screenshot(page, info, 'library-editor-narrow')
})
