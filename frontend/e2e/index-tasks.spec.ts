import { expect, test, type Page } from '@playwright/test'
import { expectToast } from './notice'

// Fixtures only: /api/index-tasks is an in-memory store below; nothing reaches a real backend.
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

/** repeat: the image was already stored before this upload (new_image false). */
interface Row { id: number; filename: string; status: 'succeeded' | 'failed'; imageId: string | null; stored: boolean; repeat?: boolean }

/** Newest first: a stored image, two whose image was deleted since, a failure. */
function seed(): Row[] {
  return [
    { id: 4, filename: 'steak.jpg', status: 'succeeded', imageId: imageId(4), stored: true },
    { id: 3, filename: 'gone-a.jpg', status: 'succeeded', imageId: imageId(3), stored: false },
    { id: 2, filename: 'junk.png', status: 'failed', imageId: null, stored: false },
    { id: 1, filename: 'gone-b.jpg', status: 'succeeded', imageId: imageId(1), stored: false },
  ]
}

function wire(row: Row) {
  const succeeded = row.status === 'succeeded'
  return {
    id: row.id, filename: row.filename, status: row.status, image_id: row.imageId, new_image: succeeded ? !row.repeat : null,
    error_code: succeeded ? null : 'INDEX_BAD_IMAGE', error_detail: succeeded ? null : 'cannot decode image',
    duration_ms: 12, trace_id: `t-${row.id}`, created_at: `2026-11-12T10:00:0${row.id}+00:00`,
    image_state: succeeded ? (row.stored ? 'present' : 'deleted') : null,
    image: row.stored ? { id: row.imageId, thumb_url: null, category_id: null, category: null, label_status: 'auto' } : null,
  }
}

async function mockApi(page: Page, seeded: Row[] = seed()) {
  let rows = seeded
  const calls = { lists: [] as string[], deletes: [] as string[] }
  const matches = (row: Row, status: string | null, state: string | null, fresh: string | null = null) =>
    (!status || row.status === status)
    && (!state || (row.status === 'succeeded' && row.stored === (state === 'present')))
    && (fresh === null || (row.status === 'succeeded' && !row.repeat === (fresh === 'true')))
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    const status = url.searchParams.get('status')
    const state = url.searchParams.get('image_state')
    if (path === '/api/index-tasks' && request.method() === 'GET') {
      calls.lists.push(url.search)
      const fresh = url.searchParams.get('new_image')
      const listed = rows.filter(row => matches(row, status, state, fresh))
      const succeeded = rows.filter(row => row.status === 'succeeded')
      return route.fulfill({ json: {
        ok: true, status, image_state: state, new_image: fresh === null ? null : fresh === 'true', page: 1, page_size: 20, total: listed.length,
        counts: {
          succeeded: succeeded.length, failed: rows.length - succeeded.length,
          image_deleted: succeeded.filter(row => !row.stored).length,
          repeated: succeeded.filter(row => row.stored && row.repeat).length,
        },
        items: listed.map(wire),
      } })
    }
    if (path === '/api/index-tasks' && request.method() === 'DELETE') {
      calls.deletes.push(url.search)
      const before = rows.length
      rows = rows.filter(row => !matches(row, status, state))
      return route.fulfill({ json: { ok: true, status, image_state: state, deleted: before - rows.length } })
    }
    const one = /^\/api\/index-tasks\/(\d+)$/.exec(path)
    if (one && request.method() === 'DELETE') {
      calls.deletes.push(path)
      const before = rows.length
      rows = rows.filter(row => row.id !== Number(one[1]))
      return route.fulfill({ json: { ok: true, id: Number(one[1]), deleted: before - rows.length } })
    }
    return route.abort('blockedbyclient')
  })
  return calls
}

async function openHistory(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^入库任务/ }).click()
  return page.getByRole('region', { name: '服务端入库记录' })
}

const rows = (panel: ReturnType<Page['getByRole']>) => panel.getByRole('region', { name: '当前页入库记录' }).getByRole('listitem')
/** The number on one of the four cards above the list. */
const metric = (panel: ReturnType<Page['getByRole']>, label: string) => panel.locator('.mg-metric').filter({ hasText: label }).locator('strong')

test('删除了图片的入库记录标为「图片已删除」，可以单独筛出来', async ({ page }) => {
  const calls = await mockApi(page)
  const panel = await openHistory(page)
  await expect(rows(panel)).toHaveCount(4)
  await expect(metric(panel, '新入库图片')).toHaveText('1')
  await expect(metric(panel, '重复上传')).toHaveText('0')
  await expect(panel.getByRole('note')).toHaveCount(0)  // nothing to explain without a repeat
  await expect(panel.locator('.mg-metric').filter({ hasText: '图片已删除' })).toContainText('2')
  const gone = rows(panel).filter({ hasText: 'gone-a.jpg' })
  await expect(gone).toHaveClass(/is-image-deleted/)
  await expect(gone.locator('.mg-job-status')).toHaveText('图片已删除')
  await expect(gone.getByRole('img', { name: '图片已删除' })).toBeVisible()
  await expect(rows(panel).filter({ hasText: 'steak.jpg' }).locator('.mg-job-status')).toHaveText('成功')

  await panel.getByRole('button', { name: '图片已删除（2）' }).click()
  await expect(rows(panel)).toHaveCount(2)
  expect(calls.lists.at(-1)).toContain('status=succeeded&image_state=deleted')
  await panel.getByRole('button', { name: '新入库（1）' }).click()
  await expect(rows(panel)).toHaveCount(1)
  expect(calls.lists.at(-1)).toContain('status=succeeded&image_state=present&new_image=true')
})

test('同一张图片上传了两次：只算一张新入库图片，多出的那条标为「重复上传」', async ({ page }) => {
  // steak.jpg was sent twice (the first request abandoned by the browser, finished by the server):
  // two succeeded records, one stored image
  const calls = await mockApi(page, [
    { id: 3, filename: 'steak.jpg', status: 'succeeded', imageId: imageId(2), stored: true, repeat: true },
    { id: 2, filename: 'steak.jpg', status: 'succeeded', imageId: imageId(2), stored: true },
    { id: 1, filename: 'salad.jpg', status: 'succeeded', imageId: imageId(1), stored: true },
  ])
  const panel = await openHistory(page)
  await expect(rows(panel)).toHaveCount(3)
  await expect(metric(panel, '新入库图片')).toHaveText('2')  // the library holds 2 images, not 3
  await expect(metric(panel, '重复上传')).toHaveText('1')
  await expect(panel.getByRole('button', { name: '全部（3）' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '新入库（2）' })).toBeVisible()
  await expect(panel.getByRole('note')).toContainText('有 1 条成功记录是重复上传')
  await expect(rows(panel).nth(0).locator('.mg-job-status')).toHaveText('重复上传')
  await expect(rows(panel).nth(1).locator('.mg-job-status')).toHaveText('成功')

  await panel.getByRole('button', { name: '查看这 1 条' }).click()
  await expect(rows(panel)).toHaveCount(1)
  expect(calls.lists.at(-1)).toContain('status=succeeded&image_state=present&new_image=false')
  await expect(panel.getByRole('button', { name: '重复上传（1）' })).toHaveAttribute('aria-pressed', 'true')
  await rows(panel).first().getByRole('button', { name: '查看详情' }).click()
  await expect(page.getByRole('dialog', { name: '入库记录详情' })).toContainText('没有新增图片')
})

test('清理图片已删除的记录：确认后只删这些记录，列表和计数随之更新', async ({ page }) => {
  const calls = await mockApi(page)
  const panel = await openHistory(page)
  await panel.getByRole('button', { name: '清理图片已删除的记录' }).click()
  const dialog = page.getByRole('dialog', { name: '清理图片已删除的记录' })
  await expect(dialog).toContainText('将删除 2 条入库记录')
  await dialog.getByRole('button', { name: '取消' }).click()
  expect(calls.deletes).toEqual([])  // cancelling sends nothing

  await panel.getByRole('button', { name: '清理图片已删除的记录' }).click()
  await page.getByRole('dialog', { name: '清理图片已删除的记录' }).getByRole('button', { name: '确认清理 2 条' }).click()
  await expectToast(page, '已清理 2 条入库记录。', 'success')
  expect(calls.deletes).toEqual(['?image_state=deleted'])
  await expect(rows(panel)).toHaveCount(2)
  await expect(rows(panel).filter({ hasText: 'gone' })).toHaveCount(0)
  await expect(panel.locator('.mg-metric').filter({ hasText: '图片已删除' })).toContainText('0')
  await expect(panel.getByRole('button', { name: '清理图片已删除的记录' })).toBeDisabled()
  await expect(panel.getByRole('button', { name: '清空失败记录' })).toBeEnabled()
})

test('详情里删除单条记录：要再确认一次，只删这一条', async ({ page }) => {
  const calls = await mockApi(page)
  const panel = await openHistory(page)
  await rows(panel).filter({ hasText: 'gone-b.jpg' }).getByRole('button', { name: '查看详情' }).click()
  const dialog = page.getByRole('dialog', { name: '入库记录详情' })
  await expect(dialog).toContainText('这张图片已从图库删除')
  await dialog.getByRole('button', { name: '删除这条记录' }).click()
  await expect(dialog).toContainText('只删这条记录，不影响图片')
  await dialog.getByRole('button', { name: '确认删除' }).click()
  await expectToast(page, '已删除这条入库记录。', 'success')
  await expect(dialog).toHaveCount(0)
  expect(calls.deletes).toEqual(['/api/index-tasks/1'])
  await expect(rows(panel)).toHaveCount(3)
  await expect(rows(panel).filter({ hasText: 'gone-b.jpg' })).toHaveCount(0)
})
