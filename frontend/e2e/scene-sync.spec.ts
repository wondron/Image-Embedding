import { expect, test, type Page } from '@playwright/test'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const scene = (id: number, code: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, code, name, description: null, include_all: false, outputs_heating: false, is_active: true,
  created_at: null, member_count: 0, labels: [], rules: [], ...extra,
})
const SCENES = [
  scene(1, 'general', '通用', { include_all: true, member_count: 3 }),
  scene(2, 'oven', '一体机', { outputs_heating: true }),
  scene(3, 'cn_diet', '中国饮食'),
]
const FIXED = '00000000-0000-4000-8000-0000000000a1'
const UNREADABLE = '00000000-0000-4000-8000-0000000000a2'
const DELETED = '00000000-0000-4000-8000-0000000000a3'

async function mockApi(page: Page, scenes: unknown[] = SCENES) {
  const reembeds: string[] = []
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes, default_scene: 'general' } })
    if (path === '/api/scenes/sync' && request.method() === 'POST') {
      return route.fulfill({ json: {
        ok: true, processed: 5, membership_changed: 1, payload_pushed: 2,
        payload_missing: [FIXED, UNREADABLE, DELETED], next_after: null, done: true,
      } })
    }
    const reembed = path.match(/^\/api\/images\/([^/]+)\/reembed$/)
    if (reembed && request.method() === 'POST') {
      const id = reembed[1]
      reembeds.push(id)
      if (id === UNREADABLE) return route.fulfill({ status: 502, json: { ok: false, error: 'IMAGE_FILE_UNAVAILABLE', detail: 'stored original could not be read', trace_id: 't' } })
      if (id === DELETED) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 't' } })
      return route.fulfill({ json: { ok: true, image_id: id, payload_version: 3, vector: { status: 'synced', collection: 'c', payload_version: 3, synced_version: 3 } } })
    }
    return route.abort('blockedbyclient')
  })
  return reembeds
}

test('全量同步：没有向量的图片可以用原图批量重新生成，读不到原图的说明原因', async ({ page }) => {
  const reembeds = await mockApi(page)
  await page.goto('/?view=scenes&sceneTab=sync')
  const panel = page.locator('.scene-sync-panel')
  await panel.getByRole('button', { name: '开始同步' }).click()
  await page.getByRole('dialog', { name: '开始全量同步' }).getByRole('button', { name: '开始同步' }).click()
  await expect(panel.locator('.scene-sync-state')).toHaveText('已完成')
  await expect(panel.locator('.scene-sync-counts')).toContainText('3没有向量')

  await panel.getByRole('button', { name: '用原图重新生成向量（3 张）' }).click()
  const result = panel.locator('.scene-sync-repair-result')
  await expect(result).toContainText('重新生成向量：已补上 1 张，1 张已被删除，1 张没成功。')
  await expect(result.getByRole('listitem')).toHaveText([new RegExp(`${UNREADABLE}\\s*读不到存储里的原图`)])
  expect(reembeds).toEqual([FIXED, UNREADABLE, DELETED])  // one after another, in list order
  // Only the one that still has no vector stays on the list
  await expect(panel.getByLabel('没有向量的图片 ID（可复制）')).toHaveValue(UNREADABLE)
  await expect(panel.locator('.scene-sync-counts')).toContainText('1没有向量')
  await expect(panel.getByRole('button', { name: '开始同步' }).or(panel.getByRole('button', { name: '从头重新同步' }))).toBeEnabled()
})

test('场景管理：先说明场景怎么起作用；选中的场景用一句话说明自己，收录规则和场景标签各一页签，全量同步在页头', async ({ page }) => {
  await mockApi(page, [
    scene(1, 'general', '通用', { include_all: true, member_count: 4 }),
    scene(2, 'oven', '一体机', {
      outputs_heating: true, member_count: 1,
      labels: [
        { id: 1, code: 'roast', name: '烤肉类', sort_order: 0, is_active: true, rule_count: 1 },
        { id: 2, code: 'steam', name: '蒸菜类', sort_order: 1, is_active: false, rule_count: 0 },
      ],
      rules: [
        { id: 1, category: { id: 5, code: 'dish.steak', name: '牛排' }, cuisine: null, label: { id: 1, code: 'roast', name: '烤肉类' } },
        { id: 2, category: null, cuisine: 'chinese', label: null },
      ],
    }),
    scene(3, 'cn_diet', '中国饮食', { is_active: false }),
  ])
  await page.goto('/?view=scenes')
  const directory = page.getByRole('complementary', { name: '场景列表' })
  const details = page.getByRole('region', { name: '场景详情' })

  // Nothing picked: what a scene is, with the catalog's own numbers
  await expect(page.getByText('场景是搜图时可选的范围')).toBeVisible()
  await expect(details.getByRole('heading', { name: '场景怎么起作用' })).toBeVisible()
  await expect(details).toContainText('全部已入库的图片，现在 4 张')
  await expect(details).toContainText('现在有 3 个场景，2 个启用')
  await expect(directory.getByRole('button', { name: /通用/ })).toContainText('全部图片')
  await expect(directory.getByRole('button', { name: /一体机/ })).toContainText('按 2 条规则收录，占图库 25%')
  await expect(directory.getByRole('button', { name: /中国饮食/ })).toContainText('已停用，搜图时不可选')

  await directory.getByRole('button', { name: /一体机/ }).click()
  await expect(details.getByRole('heading', { name: '一体机' })).toBeVisible()
  const brief = details.getByRole('region', { name: '这个场景的作用' })
  await expect(brief).toContainText('只在它收录的 1 张图片里检索')
  await expect(brief).toContainText('搜索结果按规则显示场景标签（1 个在用）')
  await expect(brief).toContainText('结果还会附带加热组和食物状态')
  await expect(brief).toContainText('1 / 4 张，占图库 25%')

  // Two tabs, one per question; the full sync is not one of them
  await expect(details.getByRole('tab')).toHaveText([/收录规则\s*2/, /场景标签\s*2/])
  await expect(details.getByRole('tab', { name: /收录规则/ })).toHaveAttribute('aria-selected', 'true')
  const rules = details.getByRole('tabpanel').getByRole('row')
  await expect(rules.nth(1)).toContainText('类别牛排dish.steak及全部下级类别')
  await expect(rules.nth(1)).toContainText('烤肉类')
  await expect(rules.nth(2)).toContainText('菜系chinese')
  await details.getByRole('tab', { name: /场景标签/ }).click()
  await expect(details.getByRole('tabpanel').getByRole('row').nth(2)).toContainText('还没有规则用它')

  await page.getByRole('button', { name: '全量同步', exact: true }).click()
  await expect(details.locator('.scene-sync-panel')).toBeVisible()
  await expect(directory.getByRole('button', { name: /一体机/ })).toHaveAttribute('aria-pressed', 'false')  // the sync is about every scene
  await details.getByRole('button', { name: '返回场景配置' }).click()
  await expect(details.getByRole('heading', { name: '一体机' })).toBeVisible()

  // A stopped scene says so instead of describing a search
  await directory.getByRole('button', { name: /中国饮食/ }).click()
  await expect(details.getByRole('region', { name: '这个场景的作用' })).toContainText('这个场景已停用')
})

test('窄屏：场景管理一次只显示一个面板，可以返回列表', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page)
  await page.goto('/?view=scenes')
  const directory = page.getByRole('complementary', { name: '场景列表' })
  const details = page.getByRole('region', { name: '场景详情' })
  await expect(directory).toBeVisible()
  await expect(details).toBeHidden()

  await directory.getByRole('button', { name: /一体机/ }).click()
  await expect(details).toBeVisible()
  await expect(directory).toBeHidden()
  await expect(details.getByRole('heading', { name: '一体机' })).toBeVisible()
  await details.getByRole('button', { name: '返回列表' }).click()
  await expect(directory).toBeVisible()
  await expect(details).toBeHidden()

  await page.getByRole('button', { name: '全量同步', exact: true }).click()
  await expect(details.locator('.scene-sync-panel')).toBeVisible()
  await expect(directory).toBeHidden()
  await details.getByRole('button', { name: '返回列表' }).click()
  await expect(directory).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
})
