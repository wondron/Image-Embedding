import { expect, test, type Page } from '@playwright/test'
import { expectToast } from './notice'

// Fixtures only: every API request is answered by the in-memory enums below.

interface Value { value: string; label: string; description: string }
interface Item { name: string; label: string; description: string; editable: boolean; values: Value[] }

const value = (code: string, label: string): Value => ({ value: code, label, description: '' })
const seededEnums = (): Item[] => [
  { name: 'food_state', label: '食物状态', description: '可为空', editable: true, values: [value('raw', '生'), value('cooked', '熟'), value('frozen', '冷冻')] },
  { name: 'label_status', label: '标注状态', description: '', editable: false, values: [value('unlabeled', '未标注'), value('auto', '自动标注'), value('verified', '已核验')] },
]

async function open(page: Page, url = '/?view=enums'): Promise<{ posts: unknown[] }> {
  const enums = seededEnums()
  const posts: unknown[] = []
  // Match the path prefix, not a '**/api/**' glob: that glob also catches Vite's /src/shared/api/* modules.
  await page.route(url_ => url_.pathname.startsWith('/api/'), route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums } })
    const append = path.match(/^\/api\/enums\/(\w+)\/values$/)
    if (append && request.method() === 'POST') {
      const item = enums.find(entry => entry.name === append[1])!
      const body = request.postDataJSON() as Value
      posts.push(body)
      item.values.push(body)
      return route.fulfill({ status: 201, json: { ok: true, enum: item } })
    }
    const detail = path.match(/^\/api\/enums\/(\w+)$/)
    const item = detail ? enums.find(entry => entry.name === detail[1]) : undefined
    if (item) return route.fulfill({ json: { ok: true, enum: item } })
    return route.abort('blockedbyclient')
  })
  await page.goto(url)
  return { posts }
}

const list = (page: Page) => page.getByRole('region', { name: '枚举列表' })
const detail = (page: Page) => page.getByRole('region', { name: '枚举详情' })
const appendButton = (page: Page) => detail(page).getByRole('button', { name: '追加值', exact: true })

test('打开即显示第一个枚举；列表分可追加和只读，上下键切换，只读的说明不能追加', async ({ page }) => {
  await open(page)
  await expect(detail(page).getByRole('heading', { name: '食物状态' })).toBeVisible()
  await expect(page).toHaveURL(/enum=food_state/)
  await expect(list(page).getByRole('heading', { name: /^可追加/ })).toBeVisible()
  await expect(list(page).getByRole('heading', { name: /^只读/ })).toBeVisible()
  await expect(detail(page).getByRole('columnheader')).toHaveText(['取值', '名称', '说明'])
  await expect(detail(page)).toContainText('3 个取值，最多 100 个')
  await expect(appendButton(page)).toBeEnabled()

  await list(page).getByRole('button', { name: /食物状态/ }).focus()
  await page.keyboard.press('ArrowDown')
  await expect(list(page).getByRole('button', { name: /标注状态/ })).toBeFocused()
  await expect(detail(page).getByRole('heading', { name: '标注状态' })).toBeVisible()
  await expect(appendButton(page)).toBeDisabled()
  await expect(detail(page)).toContainText('只读：系统按这些取值运行，不能追加。')
})

test('追加：边输边查重名和格式；追加并继续留在弹窗里，新值在表格里标出', async ({ page }) => {
  const { posts } = await open(page, '/?view=enums&enum=food_state')
  await appendButton(page).click()
  const dialog = page.getByRole('dialog', { name: '追加枚举值' })
  const input = dialog.getByLabel('取值')
  const error = dialog.locator('#enum-append-value-error')
  await expect(input).toBeFocused()
  await expect(dialog.locator('.enum-existing-list li')).toHaveText(['raw生', 'cooked熟', 'frozen冷冻'])

  await input.fill('raw')
  await expect(error).toHaveText('该取值已存在。')
  await expect(input).toHaveAttribute('aria-invalid', 'true')
  await expect(dialog.locator('.enum-existing-list li.is-match')).toHaveText('raw生')
  await input.fill('Half')
  await expect(error).toContainText('小写字母开头')
  await input.fill('half_cooked')
  await expect(error).toHaveCount(0)
  await dialog.getByLabel('名称').fill('半熟')
  await dialog.getByRole('button', { name: '追加并继续' }).click()

  await expectToast(page, '已追加「半熟」（half_cooked）。', 'success')
  await expect(dialog.getByRole('status').filter({ hasText: '本次已追加' })).toHaveText('本次已追加 1 个')
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await expect(dialog.locator('.enum-existing-list li')).toHaveCount(4)
  await expect(page.locator('#enum-value-food_state-half_cooked')).toHaveClass(/is-new/)

  await dialog.getByRole('button', { name: '完成' }).click()
  await expect(dialog).toBeHidden()
  await expect(appendButton(page)).toBeFocused()
  await expect(detail(page).getByRole('cell', { name: '半熟', exact: true })).toBeVisible()
  expect(posts).toEqual([{ value: 'half_cooked', label: '半熟', description: '' }])
})
