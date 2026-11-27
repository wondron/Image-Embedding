import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { acknowledgeNotice } from './notice'

// Fixtures only: /api/consistency answers from here. The finished report is one the backend really sent
// (backend/tests/test_consistency.py's scenario through the real routes).
const FINISHED = JSON.parse(readFileSync('src/entities/consistency/__fixtures__/report.json', 'utf8'))

const OPTIONS = { vectors: true, files: true, orphans: true, content: false, encoders: true }
const running = (phase: string, done: number, total: number | null, state = 'running') => ({
  ok: true,
  check: {
    state, started_at: FINISHED.check.started_at, finished_at: null, options: OPTIONS,
    progress: { phase, done, total, label: null }, error: null, report: null,
  },
})

async function mockApi(page: Page) {
  const calls: { method: string; path: string; body: unknown }[] = []
  let reads = 0
  let started = false
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    if (!path.startsWith('/api/consistency')) return route.abort('blockedbyclient')
    calls.push({ method: request.method(), path, body: request.method() === 'POST' ? request.postDataJSON() : null })
    if (path === '/api/consistency/check') {
      started = true
      return route.fulfill({ json: running('starting', 0, null) })
    }
    if (!started) return route.fulfill({ json: { ok: true, check: null } })
    reads += 1
    // the background check moves on between reads, then finishes
    return route.fulfill({ json: reads === 1 ? running('images', 1500, 15231) : FINISHED })
  })
  return calls
}

test('数据核对 runs a check in the background and says what to fix and where', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/?view=consistency')
  await expect(page.getByRole('heading', { level: 1, name: '数据核对' })).toBeVisible()
  await expect(page.getByText('还没有核对过')).toBeVisible()

  // content needs files; it stays off by default (it downloads every original)
  await expect(page.getByLabel(/下载原图核对内容/)).not.toBeChecked()
  await page.getByRole('button', { name: '开始核对' }).click()
  await expect(page.getByLabel('核对进度')).toContainText('逐张核对图片（向量和文件） · 1500 / 15231')
  await expect(page.getByRole('button', { name: '停止' })).toBeVisible()

  await acknowledgeNotice(page, /核对完成，有 \d+ 项要看/, 'warn')
  await expect(page.getByText(/有 \d+ 项不一致或查不了/)).toBeVisible()
  const connections = page.getByLabel('本进程连着的服务')
  await expect(connections).toContainText(':memory:')
  await expect(connections).toContainText('pg / image_embedding')
  await expect(connections).toContainText('dino:v1')

  const issues = page.getByLabel('要处理的事')
  await expect(issues).toContainText('1 张图片的原图不在存储里')
  await expect(issues).toContainText('dino:v1：1 张的向量丢了')
  await expect(issues).toContainText('dino:v2 和当前版本有 3 张图不一样')
  await expect(issues).toContainText('1 个已下线版本的 collection 还在')
  // the first ids are listed and can be copied
  const lost = issues.locator('li').filter({ hasText: 'dino:v1：1 张的向量丢了' })
  await lost.getByText('列出 1 个').click()
  await expect(lost.locator('code')).toHaveText('00000000-0000-0000-0000-000000000002')

  const table = page.getByLabel('各版本的向量')
  await expect(table.getByRole('row')).toHaveCount(4)
  await expect(table.getByRole('row', { name: /dino:v3/ })).toContainText('未完成')
  await expect(page.getByLabel('对象存储里的文件')).toContainText('原图不在')

  await expect(table).toContainText('存储里这几张的原图都读不到，没能重算')  // the backend's reason, in words
  await page.setViewportSize({ width: 1440, height: 4200 })  // the app scrolls inside its own container
  await page.screenshot({ path: 'test-results/consistency-check.png', fullPage: true })

  expect(calls.find(call => call.method === 'POST')?.body).toEqual(OPTIONS)

  // an issue sends the person to the page that fixes it
  await lost.getByRole('button', { name: /去「模型版本」/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: '模型版本' })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('view')).toBe('models')
})
