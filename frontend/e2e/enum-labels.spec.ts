import { expect, test, type Page, type Route } from '@playwright/test'

// Fixtures only: every API and remote image request is intercepted below.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const initialValues = [
  { value: 'raw', label: '生', description: '' },
  { value: 'cooked', label: '熟', description: '' },
  { value: 'frozen', label: '冷冻', description: '' },
]
const thawed = { value: 'thawed', label: '解冻', description: '' }
const foodStateEnum = (values: typeof initialValues) => ({
  name: 'food_state', label: '食物状态', description: '可为空', editable: true, values,
})
/** One self-built hit of a heating scene (schemas/retrieval.py SearchHit). */
const hit = (n: number, food_state: string | null) => ({
  id: `0b6f2c1e-0000-4000-8000-00000000000${n}`,
  original_url: `https://images.test/original-${n}.png`,
  thumb_url: `https://images.test/thumb-${n}.png`,
  similarity: 0.9 - n / 100,
  category_id: n,
  category: `食材${n}`,
  scenes: ['general', 'oven'],
  scene_label: `烤食材${n}`,
  label_status: 'verified',
  description: '',
  width: 640,
  height: 480,
  food_state,
  heating_group: { id: 3, code: 'veg_roast', name: '蔬菜烘烤' },
})
const searchBody = (items: unknown[]) => ({
  ok: true, trace_id: 'e2e-enum-labels', source: 'self', scene: 'oven', outputs_heating: true, items,
})

async function mockBackend(page: Page, { enums, items }: { enums: (route: Route) => Promise<void>; items: unknown[] }) {
  // Match the path prefix, not a '**/api/**' glob: that glob also catches Vite's /src/shared/api/* modules.
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/search') return route.fulfill({ json: searchBody(items) })
    if (path.startsWith('/api/enums')) return enums(route)
    return route.abort('blockedbyclient')
  })
  await page.route('https://images.test/**', route => route.fulfill({ contentType: 'image/png', body: png }))
}

async function searchByUrl(page: Page) {
  await page.getByRole('button', { name: '图片链接', exact: true }).click()
  await page.getByLabel('图片地址').fill('https://images.test/query.png')
  await page.getByRole('button', { name: '开始搜索', exact: true }).click()
}

async function navigate(page: Page, name: string) {
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
}

test('食物状态显示 /api/enums 里的中文名，没有中文名的取值原样显示', async ({ page }) => {
  await mockBackend(page, {
    enums: route => route.fulfill({ json: { ok: true, enums: [foodStateEnum([...initialValues, thawed])] } }),
    items: [hit(1, 'thawed'), hit(2, 'raw'), hit(3, 'half_baked'), hit(4, null)],
  })
  await page.goto('/')
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(4)
  await expect(cards.nth(0)).toContainText('状态 解冻')
  await expect(cards.nth(1)).toContainText('状态 生')
  await expect(cards.nth(2)).toContainText('状态 half_baked')
  await expect(cards.nth(3)).toContainText('状态 —')
})

test('/api/enums 读取失败时食物状态仍按原始取值显示', async ({ page }) => {
  await mockBackend(page, {
    enums: route => route.fulfill({
      status: 503, json: { ok: false, error: 'ENUM_FILE_INVALID', detail: 'enums.yaml: invalid YAML', trace_id: 'e2e' },
    }),
    items: [hit(1, 'thawed'), hit(2, 'raw')],
  })
  const enumsAnswered = page.waitForResponse(response => new URL(response.url()).pathname === '/api/enums')
  await page.goto('/')
  await enumsAnswered
  await searchByUrl(page)
  const cards = page.locator('.result-card')
  await expect(cards.nth(0)).toContainText('状态 thawed')
  await expect(cards.nth(1)).toContainText('状态 raw')
})

test('在枚举配置页追加取值后，其他页面立即显示新取值的中文名', async ({ page }) => {
  let values = [...initialValues]
  await mockBackend(page, {
    enums: async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (request.method() === 'POST' && path === '/api/enums/food_state/values') {
        const body = request.postDataJSON() as typeof thawed
        values = [...values, { value: body.value, label: body.label, description: body.description }]
        return route.fulfill({ status: 201, json: { ok: true, enum: foodStateEnum(values) } })
      }
      if (path === '/api/enums/food_state') return route.fulfill({ json: { ok: true, enum: foodStateEnum(values) } })
      return route.fulfill({ json: { ok: true, enums: [foodStateEnum(values)] } })
    },
    items: [hit(1, 'thawed')],
  })
  await page.goto('/')
  await searchByUrl(page)
  const card = page.locator('.result-card').first()
  await expect(card).toContainText('状态 thawed')

  await navigate(page, '枚举配置')
  await page.getByRole('button', { name: /食物状态/ }).click()
  await page.getByRole('button', { name: '追加值', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '追加枚举值' })
  await dialog.getByLabel('取值').fill('thawed')
  await dialog.getByLabel('名称').fill('解冻')
  await dialog.getByRole('button', { name: '追加', exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('cell', { name: '解冻', exact: true })).toBeVisible()

  await navigate(page, '图搜图')
  await expect(card).toContainText('状态 解冻')
})
