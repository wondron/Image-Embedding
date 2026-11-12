import { expect, test, type Page } from '@playwright/test'
import { acknowledgeNotice, expectToast } from './notice'

// Fixtures only: every API request is answered below; nothing reaches a real backend.
const imageId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

interface Queued { id: string; deletedAt: string }

/** failing: ids whose cleanup keeps failing (storage down for them). */
async function mockApi(page: Page, { queued = 3, failing = [] as string[], ready = true } = {}) {
  const queue: Queued[] = Array.from({ length: queued }, (_, index) => ({ id: imageId(index + 1), deletedAt: `2026-11-11T0${index}:00:00+00:00` }))
  const calls = { lists: 0, runs: [] as number[] }
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    if (path === '/api/index-tasks') {
      return route.fulfill({ json: { ok: true, items: [], total: 0, counts: { succeeded: 0, failed: 0 }, page: 1, page_size: 20, status: null } })
    }
    if (path === '/api/image-deletions' && request.method() === 'GET') {
      calls.lists += 1
      const limit = Number(url.searchParams.get('limit'))
      return route.fulfill({
        json: {
          ok: true, total: queue.length, cleanup_ready: ready,
          items: queue.slice(0, limit).map(item => ({ image_id: item.id, original_url: null, thumb_url: null, deleted_at: item.deletedAt })),
        },
      })
    }
    if (path === '/api/image-deletions/process' && request.method() === 'POST') {
      if (!ready) return route.fulfill({ status: 503, json: { ok: false, error: 'NOT_READY', detail: 'retrieval stack is not initialized', trace_id: 'e2e-clean' } })
      const { limit } = request.postDataJSON() as { limit: number }
      calls.runs.push(limit)
      const batch = queue.slice(0, limit)
      const processed = batch.filter(item => !failing.includes(item.id)).map(item => item.id)
      for (const id of processed) queue.splice(queue.findIndex(item => item.id === id), 1)
      const failed = batch.filter(item => failing.includes(item.id))
        .map(item => ({ image_id: item.id, error: 'IMAGE_STORAGE_FAILED', detail: '对象存储暂时不可用' }))
      return route.fulfill({ json: { ok: true, scanned: batch.length, processed, failed, remaining: queue.length } })
    }
    return route.abort('blockedbyclient')
  })
  return { queue, calls }
}

async function openDeletions(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^入库任务/ }).click()
  await page.getByRole('tab', { name: '待清理图片' }).click()
  return page.getByRole('region', { name: '待清理图片' })
}

const rows = (panel: ReturnType<Page['getByRole']>) => panel.getByRole('region', { name: '待清理的图片' }).getByRole('listitem')

test('待清理图片：列出队列，立即清理后说明结果，失败的留在队列里并写明原因', async ({ page }) => {
  const { calls } = await mockApi(page, { queued: 3, failing: [imageId(3)] })
  const panel = await openDeletions(page)
  await expect(panel.locator('.mg-metric').filter({ hasText: '待清理' })).toContainText('3')
  await expect(rows(panel)).toHaveCount(3)
  await expect(rows(panel).first()).toContainText(imageId(1))

  await panel.getByRole('button', { name: '立即清理' }).click()
  await acknowledgeNotice(page, '已清理 2 张；1 张清理失败，仍留在队列里：对象存储暂时不可用。', 'warn')
  // The second run cleaned nothing (only the failing one was left), so the click stopped there
  expect(calls.runs).toEqual([50, 50])
  await expect(rows(panel)).toHaveCount(1)
  await expect(rows(panel).first()).toContainText('清理失败：对象存储暂时不可用（IMAGE_STORAGE_FAILED）')
  await expect(panel.locator('.mg-metric').filter({ hasText: '待清理' })).toContainText('1')
})

test('待清理图片：全部清理完是轻提示，队列为空时说明已清理完', async ({ page }) => {
  await mockApi(page, { queued: 2 })
  const panel = await openDeletions(page)
  await panel.getByRole('button', { name: '立即清理' }).click()
  await expectToast(page, '已清理 2 张图片的向量和文件，队列已清空。', 'success')
  await expect(panel.getByRole('heading', { name: '没有待清理的图片' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '立即清理' })).toBeDisabled()
})

test('待清理图片：检索服务未就绪时不能清理，并说明原因', async ({ page }) => {
  const { calls } = await mockApi(page, { queued: 2, ready: false })
  const panel = await openDeletions(page)
  await expect(panel.locator('.mg-metric').filter({ hasText: '能否清理' })).toContainText('暂时不能')
  await expect(panel.getByRole('button', { name: '立即清理' })).toBeDisabled()
  expect(calls.runs).toEqual([])
})
