import { expect, test, type Page, type Route } from '@playwright/test'

// Fixtures only: every API request is answered by the in-memory backend below.
// It follows backend/app/schemas/categories.py closely enough to drive the page.

interface Name { lang: string; name: string; kind: 'primary' | 'alias' }
interface Row {
  id: number
  code: string
  parent_id: number | null
  names: Name[]
  cuisines: string[]
  heating_group_id: number | null
  sort_order: number
  is_active: boolean
  image_count: number
  scene_rule_count: number
}
interface Call { method: string; path: string; body: unknown }

const ROOT_CODES = ['ingredient', 'dish', 'dessert', 'beverage']
const HEATING_GROUPS = [{ id: 2, code: 'grill', name: '烤' }]
const DOMAIN_ENUM = {
  name: 'category_domain', label: '类别域', description: '', editable: false,
  values: [
    { value: 'ingredient', label: '食材', description: '' },
    { value: 'dish', label: '菜品', description: '' },
    { value: 'dessert', label: '甜点烘焙', description: '' },
    { value: 'beverage', label: '饮品', description: '' },
  ],
}
const zh = (name: string): Name => ({ lang: 'zh', name, kind: 'primary' })
const row = (id: number, code: string, parent_id: number | null, name: string, extra: Partial<Row> = {}): Row => ({
  id, code, parent_id, names: [zh(name)], cuisines: [], heating_group_id: null, sort_order: 0,
  is_active: true, image_count: 0, scene_rule_count: 0, ...extra,
})
/** 菜品(1) > 披萨(2) > 披萨切片(4); 菜品 > 面条(3); 食材(5) */
const seededRows = (): Row[] => [
  row(1, 'dish', null, '菜品'),
  row(2, 'dish.pizza', 1, '披萨', { cuisines: ['italian'] }),
  row(3, 'dish.noodle', 1, '面条', { image_count: 3 }),
  row(4, 'dish.pizza.slice', 2, '披萨切片'),
  row(5, 'ingredient', null, '食材', { sort_order: 1 }),
]
const errorBody = (error: string, detail: string) => ({ ok: false, error, detail, trace_id: 'e2e-category' })

class Backend {
  rows: Row[]
  calls: Call[] = []
  /** While set, every GET /api/categories gets this answer instead of the list. */
  listFailure: { status: number; json: unknown } | null = null

  constructor(rows: Row[]) {
    this.rows = rows
  }

  private depth(row: Row): number {
    let depth = 0
    for (let parent = this.rows.find(r => r.id === row.parent_id); parent; parent = this.rows.find(r => r.id === parent!.parent_id)) depth += 1
    return depth
  }

  private path(row: Row): string {
    const parent = this.rows.find(r => r.id === row.parent_id)
    return parent ? `${this.path(parent)}.${row.id}` : String(row.id)
  }

  private item(row: Row) {
    const { heating_group_id, ...rest } = row
    return {
      ...rest,
      path: this.path(row),
      name: row.names.find(n => n.lang === 'zh' && n.kind === 'primary')?.name ?? null,
      heating_group: HEATING_GROUPS.find(group => group.id === heating_group_id) ?? null,
      created_at: '2026-11-10T08:00:00+00:00',
      updated_at: '2026-11-10T09:30:00+00:00',
    }
  }

  private list() {
    const sorted = [...this.rows].sort((a, b) => this.depth(a) - this.depth(b) || a.sort_order - b.sort_order || a.id - b.id)
    return { ok: true, categories: sorted.map(r => this.item(r)), heating_groups: HEATING_GROUPS, root_codes: ROOT_CODES }
  }

  async handle(route: Route): Promise<void> {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : undefined
    this.calls.push({ method, path, body })
    if (method === 'GET' && path === '/api/categories') {
      return route.fulfill(this.listFailure ?? { json: this.list() })
    }
    if (method === 'POST' && path === '/api/categories') {
      const input = body as Omit<Row, 'id' | 'image_count' | 'scene_rule_count'>
      if (this.rows.some(r => r.code === input.code)) {
        return route.fulfill({ status: 409, json: errorBody('CATEGORY_CODE_EXISTS', `another category already uses the code '${input.code}'`) })
      }
      const created: Row = { ...input, id: Math.max(0, ...this.rows.map(r => r.id)) + 1, image_count: 0, scene_rule_count: 0 }
      this.rows.push(created)
      return route.fulfill({ status: 201, json: { ok: true, category: this.item(created) } })
    }
    const target = this.rows.find(r => `/api/categories/${r.id}` === path)
    if (!target) return route.fulfill({ status: 404, json: errorBody('CATEGORY_NOT_FOUND', 'category does not exist') })
    if (method === 'PATCH') {
      Object.assign(target, body)
      return route.fulfill({ json: { ok: true, category: this.item(target) } })
    }
    if (method === 'DELETE') {
      if (this.rows.some(r => r.parent_id === target.id) || target.image_count > 0 || target.scene_rule_count > 0) {
        return route.fulfill({ status: 409, json: errorBody('CATEGORY_IN_USE', `category '${target.code}' is still in use`) })
      }
      this.rows = this.rows.filter(r => r !== target)
      return route.fulfill({ json: { ok: true, deleted: { id: target.id, code: target.code } } })
    }
    return route.abort('blockedbyclient')
  }

  writes(): Call[] {
    return this.calls.filter(call => call.method !== 'GET')
  }
}

async function open(page: Page, rows: Row[], url = '/?view=categories'): Promise<Backend> {
  const backend = new Backend(rows)
  // Match the path prefix, not a '**/api/**' glob: that glob also catches Vite's /src/shared/api/* modules.
  await page.route(url_ => url_.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [DOMAIN_ENUM] } })
    if (path.startsWith('/api/categories')) return backend.handle(route)
    return route.abort('blockedbyclient')
  })
  await page.goto(url)
  return backend
}

const tree = (page: Page) => page.getByRole('region', { name: '类别树' })
const detail = (page: Page) => page.getByRole('region', { name: '类别详情' })
/** The row button of a node; its name starts with the label and the code. */
const nodeButton = (page: Page, label: string, code: string) => tree(page).getByRole('button', { name: new RegExp(`^${label} ${code.replace(/\./g, '\\.')}( |$)`) })

test('空库：先建根类别（域），再在它下面建子类别', async ({ page }) => {
  const backend = await open(page, [])
  await expect(tree(page).getByRole('heading', { name: '还没有类别' })).toBeVisible()

  await tree(page).getByRole('button', { name: '新建根类别' }).click()
  const rootDialog = page.getByRole('dialog', { name: '新建根类别' })
  // The root codes come from the API; their Chinese names from the category_domain enum.
  await expect(rootDialog.locator('#category-form-code option')).toHaveText(['食材（ingredient）', '菜品（dish）', '甜点烘焙（dessert）', '饮品（beverage）'])
  await expect(rootDialog.locator('#category-form-zhName')).toHaveValue('食材')
  await rootDialog.locator('#category-form-code').selectOption('dish')
  await expect(rootDialog.locator('#category-form-zhName')).toHaveValue('菜品')
  await rootDialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(rootDialog).toBeHidden()
  await expect(page.getByRole('status').filter({ hasText: '已创建「菜品」。' })).toBeVisible()
  await expect(nodeButton(page, '菜品', 'dish')).toHaveAttribute('aria-current', 'true')
  expect(backend.writes()[0]).toEqual({
    method: 'POST',
    path: '/api/categories',
    body: { code: 'dish', parent_id: null, names: [zh('菜品')], cuisines: [], heating_group_id: null, sort_order: 0, is_active: true },
  })

  await detail(page).getByRole('button', { name: '新建子类别' }).click()
  const childDialog = page.getByRole('dialog', { name: '新建子类别' })
  await expect(childDialog.getByText('菜品（dish）')).toBeVisible()
  const code = childDialog.locator('#category-form-code')
  await expect(code).toHaveValue('dish.')
  await expect(code).toBeFocused()
  await page.keyboard.type('pizza') // the caret is after the prefix
  await childDialog.locator('#category-form-zhName').fill('披萨')
  await childDialog.getByRole('button', { name: '添加名称' }).click()
  await childDialog.getByLabel('第 1 行语言代码', { exact: true }).fill('EN') // typed upper-case, stored lower-case
  await childDialog.getByLabel('第 1 行名称', { exact: true }).fill('Pizza')
  await childDialog.getByLabel('第 1 行类型', { exact: true }).selectOption('primary')
  await childDialog.locator('#category-form-cuisines').fill('italian， american')
  await childDialog.locator('#category-form-heatingGroupId').selectOption('2')
  await childDialog.locator('#category-form-sortOrder').fill('3')
  await childDialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(childDialog).toBeHidden()
  expect(backend.writes()[1].body).toEqual({
    code: 'dish.pizza',
    parent_id: 1,
    names: [zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
    cuisines: ['italian', 'american'],
    heating_group_id: 2,
    sort_order: 3,
    is_active: true,
  })

  // The new node is selected, its parent opened, and the detail shows what was stored.
  await expect(nodeButton(page, '披萨', 'dish.pizza')).toHaveAttribute('aria-current', 'true')
  await expect(tree(page).getByRole('button', { name: '收起「菜品」' })).toBeVisible()
  await expect(detail(page).getByRole('heading', { name: '披萨' })).toBeVisible()
  await expect(detail(page)).toContainText('enPizza主名')
  await expect(detail(page)).toContainText('italian、american')
  await expect(detail(page)).toContainText('烤（grill）')
  await expect(page).toHaveURL(/[?&]view=categories&category=2$/)
})

test('编辑只提交改过的字段；换上级时子类别一起移动', async ({ page }) => {
  const backend = await open(page, seededRows())
  await expect(nodeButton(page, '披萨', 'dish.pizza')).toBeVisible() // roots are open after the first load
  await nodeButton(page, '披萨', 'dish.pizza').click()
  await expect(detail(page)).toContainText('菜品') // 上级
  await expect(detail(page)).toContainText('italian')

  await detail(page).getByRole('button', { name: '编辑' }).click()
  const dialog = page.getByRole('dialog', { name: '编辑类别' })
  await expect(dialog.locator('#category-form-zhName')).toBeFocused()
  // A node cannot move under itself or its own subtree: those are not offered.
  await expect(dialog.locator('#category-form-parentId option')).toHaveText(['菜品（dish）', '　面条（dish.noodle）', '食材（ingredient）'])
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.getByRole('status')).toHaveText('没有改动，无需保存。')
  expect(backend.writes()).toEqual([])

  await dialog.locator('#category-form-sortOrder').fill('5')
  await dialog.getByRole('checkbox', { name: '启用' }).uncheck()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/api/categories/2', body: { sort_order: 5, is_active: false } }])
  await expect(nodeButton(page, '披萨', 'dish.pizza')).toContainText('停用')

  await detail(page).getByRole('button', { name: '编辑' }).click()
  await dialog.locator('#category-form-parentId').selectOption('5')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(backend.writes()[1]).toEqual({ method: 'PATCH', path: '/api/categories/2', body: { parent_id: 5 } })
  // 披萨 now sits under 食材 (opened to show it) and brought 披萨切片 with it.
  const ingredient = tree(page).getByRole('listitem').filter({ has: page.getByRole('button', { name: /^食材 ingredient( |$)/ }) })
  await expect(ingredient.getByRole('button', { name: /^披萨 dish\.pizza / })).toBeVisible()
  await tree(page).getByRole('button', { name: '展开「披萨」' }).click()
  await expect(ingredient.getByRole('button', { name: /^披萨切片 / })).toBeVisible()
  await expect(detail(page).getByRole('button', { name: '食材', exact: true })).toBeVisible() // 上级 breadcrumb
})

test('表单在发请求前校验，服务端拒绝时把原因显示在对应字段', async ({ page }) => {
  const backend = await open(page, seededRows())
  await nodeButton(page, '菜品', 'dish').click()
  await detail(page).getByRole('button', { name: '新建子类别' }).click()
  const dialog = page.getByRole('dialog', { name: '新建子类别' })
  const code = dialog.locator('#category-form-code')

  await code.fill('Dish.Rice')
  await expect(code).toHaveValue('Dish.Rice') // filling replaces the "dish." prefix like any input
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(dialog.locator('#category-form-code-error')).toContainText('小写字母')
  await expect(dialog.locator('#category-form-zhName-error')).toHaveText('请填写中文名称。')
  await expect(code).toBeFocused()
  await code.fill('dish.pizza')
  await expect(dialog.locator('#category-form-code-error')).toBeHidden() // editing a field clears its error
  await dialog.locator('#category-form-zhName').fill('米饭')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(dialog.locator('#category-form-code-error')).toHaveText('该编码已被其他类别使用。')
  expect(backend.writes()).toEqual([])

  // Somebody else created dish.rice after this page loaded its tree: the server refuses.
  backend.rows.push(row(9, 'dish.rice', 1, '米饭'))
  await code.fill('dish.rice')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(dialog.locator('#category-form-code-error')).toHaveText('该编码已被其他类别使用。')
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('#category-form-zhName')).toHaveValue('米饭') // what was typed is kept
  expect(backend.writes()).toHaveLength(1)
})

test('被使用的类别不能删除；空类别确认后删除', async ({ page }) => {
  const backend = await open(page, seededRows())
  await nodeButton(page, '面条', 'dish.noodle').click()
  await expect(detail(page).getByRole('button', { name: '删除' })).toBeDisabled()
  await expect(detail(page)).toContainText('还有 3 张图片 在使用这个类别，不能删除')
  await nodeButton(page, '披萨', 'dish.pizza').click()
  await expect(detail(page)).toContainText('还有 1 个子类别 在使用这个类别，不能删除')

  await tree(page).getByRole('button', { name: '展开「披萨」' }).click()
  await nodeButton(page, '披萨切片', 'dish.pizza.slice').click()
  await detail(page).getByRole('button', { name: '删除' }).click()
  const confirm = page.getByRole('dialog', { name: '删除类别' })
  await expect(confirm).toContainText('确定删除「披萨切片」（dish.pizza.slice）吗？')
  await confirm.getByRole('button', { name: '取消' }).click()
  await expect(confirm).toBeHidden()
  expect(backend.writes()).toEqual([])

  await detail(page).getByRole('button', { name: '删除' }).click()
  await confirm.getByRole('button', { name: '删除', exact: true }).click()
  await expect(confirm).toBeHidden()
  expect(backend.writes()).toEqual([{ method: 'DELETE', path: '/api/categories/4', body: undefined }])
  await expect(page.getByRole('status').filter({ hasText: '已删除「披萨切片」。' })).toBeVisible()
  await expect(nodeButton(page, '披萨切片', 'dish.pizza.slice')).toHaveCount(0)
  // The parent is selected next, and without children it can now be deleted too.
  await expect(detail(page).getByRole('heading', { name: '披萨' })).toBeVisible()
  await expect(detail(page).getByRole('button', { name: '删除' })).toBeEnabled()
})

test('列表加载失败时显示错误码和追踪号，重试后恢复；链接可以直接打开某个类别', async ({ page }) => {
  const backend = new Backend(seededRows())
  // Not "the first request": in development StrictMode sends (and aborts) one request before the real one.
  backend.listFailure = { status: 503, json: errorBody('NOT_READY', 'retrieval stack is not initialized') }
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [DOMAIN_ENUM] } })
    return backend.handle(route)
  })
  await page.goto('/?view=categories&category=4')
  const alert = page.getByRole('alert').filter({ hasText: '类别树加载失败' })
  await expect(alert).toContainText('NOT_READY')
  await expect(alert).toContainText('retrieval stack is not initialized')
  await expect(alert).toContainText('e2e-category')
  await expect(tree(page).getByRole('button', { name: '新建根类别' })).toBeDisabled()

  backend.listFailure = null
  await alert.getByRole('button', { name: '重试' }).click()
  await expect(alert).toBeHidden()
  // ?category=4 selects 披萨切片 and opens the path down to it.
  await expect(nodeButton(page, '披萨切片', 'dish.pizza.slice')).toHaveAttribute('aria-current', 'true')
  await expect(detail(page).getByRole('heading', { name: '披萨切片' })).toBeVisible()
  await expect(tree(page).getByRole('button', { name: '新建根类别' })).toBeEnabled() // dessert and beverage are still free
})

test('窄屏：先看类别树，选中后进入详情并可返回', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 800 })
  await open(page, seededRows())
  await expect(tree(page)).toBeVisible()
  await expect(detail(page)).toBeHidden()
  await nodeButton(page, '食材', 'ingredient').click()
  await expect(detail(page).getByRole('heading', { name: '食材' })).toBeVisible()
  await expect(tree(page)).toBeHidden()
  await detail(page).getByRole('button', { name: '返回类别树' }).click()
  await expect(tree(page)).toBeVisible()
})
