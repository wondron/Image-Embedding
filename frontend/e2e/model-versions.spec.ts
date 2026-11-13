import { expect, test, type Page } from '@playwright/test'
import { acknowledgeNotice, expectToast } from './notice'

// Fixtures only: /api/embedding-models is an in-memory model of the backend below; nothing reaches a real backend.
interface Row {
  id: number
  name: string
  version: string
  status: 'building' | 'ready' | 'current' | 'retired'
  collection: string
  embedded: number
  activatedAt: string | null
  weights: string
}

const IMAGES = 6
const NEW_WEIGHTS = 'config/model/dino/multitask_cls_v2.onnx'

async function mockApi(page: Page, { stalled = false } = {}) {
  const rows: Row[] = [
    { id: 1, name: 'dino', version: 'v1', status: 'current', collection: 'dino_v1', embedded: IMAGES, activatedAt: '2026-11-01T08:00:00+08:00', weights: 'config/model/dino/multitask_cls.onnx' },
  ]
  let configuredId: number | null = null
  let servingId: number | null = stalled ? null : 1
  let build: Record<string, unknown> | null = null
  const calls: string[] = []
  const wire = (row: Row) => ({
    id: row.id, name: row.name, version: row.version, status: row.status, collection: row.collection, dim: 1024,
    distance: 'cosine', weights_uri: row.weights, created_at: '2026-11-01T08:00:00+08:00', activated_at: row.activatedAt,
    embedded: row.status === 'retired' ? 0 : row.embedded, missing: row.status === 'retired' ? 0 : IMAGES - row.embedded, behind: 0,
    fingerprint: true, configured: row.id === configuredId, serving: row.id === servingId,
    encoder: row.id === configuredId ? 'configured' : row.id === servingId ? 'serving' : 'recorded',
  })
  const overview = () => ({
    ok: true, images: IMAGES,
    configured: { loaded: true, model_path: NEW_WEIGHTS, dim: 1024, model_id: configuredId, detail: null },
    retrieval: servingId === null
      ? { ready: false, model_id: null, encoder: null, code: 'EMBEDDING_MODEL_MISMATCH', detail: 'EMBEDDING_MODEL_MISMATCH: the weights of dino:v1 could not be loaded' }
      : { ready: true, model_id: servingId, encoder: servingId === configuredId ? 'configured' : 'own', code: null, detail: null },
    build, models: rows.map(wire),
  })
  const row = (id: number) => rows.find(r => r.id === id)!
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (path === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    if (!path.startsWith('/api/embedding-models')) return route.abort('blockedbyclient')
    calls.push(`${request.method()} ${path}`)
    if (request.method() === 'GET') {
      if (build && build.state === 'running') {  // the background build moves on between reads
        const target = row(build.model_id as number)
        target.embedded = Math.min(IMAGES, target.embedded + 3)
        build = { ...build, embedded: (build.embedded as number) + 3 }
        if (target.embedded === IMAGES) {
          build = { ...build, state: 'done', finished_at: '2026-11-12T10:01:00+08:00', remaining: 0 }
          if (target.status === 'building') target.status = 'ready'
        }
      }
      return route.fulfill({ json: overview() })
    }
    if (path === '/api/embedding-models') {
      const body = request.postDataJSON() as { name?: string; version?: string }
      const created: Row = { id: 2, name: body.name ?? 'model', version: body.version ?? 'x', status: 'building', collection: `${body.name}_${body.version}`.toLowerCase(), embedded: 0, activatedAt: null, weights: NEW_WEIGHTS }
      rows.push(created)
      configuredId = 2
      return route.fulfill({ status: 201, json: { ok: true, model: wire(created) } })
    }
    const match = /^\/api\/embedding-models\/(\d+)\/(build|build\/stop|activate|retire)$/.exec(path)!
    const id = Number(match[1])
    const target = row(id)
    if (match[2] === 'build') {
      build = { model_id: id, state: 'running', started_at: '2026-11-12T10:00:00+08:00', finished_at: null, embedded: 0, failed: 0, gone: 0, payloads: 0, passes: 1, remaining: null, error: null, failures: [] }
      return route.fulfill({ json: { ok: true, build } })
    }
    if (match[2] === 'activate') {
      const { allow_missing } = request.postDataJSON() as { allow_missing: boolean }
      if (target.embedded < IMAGES && !allow_missing) {
        return route.fulfill({ status: 409, json: { ok: false, error: 'EMBEDDING_MODEL_INCOMPLETE', detail: `${IMAGES - target.embedded} stored images have no vector`, trace_id: 'e2e-incomplete' } })
      }
      const previous = rows.find(r => r.status === 'current' && r.id !== id) ?? null
      if (previous) previous.status = 'ready'
      target.status = 'current'
      target.activatedAt = '2026-11-12T10:05:00+08:00'
      servingId = id
      return route.fulfill({ json: { ok: true, model: wire(target), previous_id: previous?.id ?? null, switched: true } })
    }
    if (match[2] === 'retire') {
      const ledger = target.embedded
      target.status = 'retired'
      target.embedded = 0
      return route.fulfill({ json: { ok: true, model: wire(target), dropped: true, ledger_rows: ledger } })
    }
    return route.fulfill({ json: { ok: true, build } })
  })
  return { calls, rows }
}

async function openModels(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '模型版本' }).click()
  return page.getByRole('region', { name: '模型版本' })
}

const versionRow = (panel: ReturnType<Page['getByRole']>, label: string) =>
  panel.getByRole('region', { name: '全部模型版本' }).getByRole('listitem', { name: label })

test('换模型：登记配置里的新模型 → 后台生成向量 → 切换；旧版本保留，可切回，再下线', async ({ page }) => {
  const { calls } = await mockApi(page)
  const panel = await openModels(page)
  await expect(panel.locator('.mg-metric').filter({ hasText: '检索在用' })).toContainText('dino:v1')
  await expect(panel.locator('.mg-metric').filter({ hasText: '配置的模型' })).toContainText('未登记的新模型')
  const guide = panel.locator('.model-guide')
  await expect(guide).toContainText('配置里换了一个新的 embedding 模型')
  await expect(guide).toContainText('检索继续用 dino:v1')
  await expect(guide.getByRole('listitem').first()).toHaveAttribute('aria-current', 'step')

  // 1. register: the name comes from the weights file, the version from now
  await guide.getByRole('button', { name: '登记为新版本' }).click()
  const register = page.getByRole('dialog', { name: '登记新的模型版本' })
  await expect(register).toContainText(NEW_WEIGHTS)
  await expect(register.getByLabel('名称')).toHaveValue('multitask_cls_v2')
  await register.getByLabel('版本').fill('a b')
  await expect(register.getByRole('button', { name: '登记' })).toBeDisabled()
  await register.getByLabel('版本').fill('v2')
  await register.getByRole('button', { name: '登记' }).click()
  await expectToast(page, '已登记 multitask_cls_v2:v2', 'success')
  await expect(versionRow(panel, 'multitask_cls_v2:v2')).toContainText('未完成')
  await expect(versionRow(panel, 'multitask_cls_v2:v2')).toContainText('0 / 6 张有向量')

  // 2. build: progress polls until done, then the version is ready to switch
  await expect(guide).toContainText('为 multitask_cls_v2:v2 生成向量')
  await guide.getByRole('button', { name: '生成向量' }).click()
  await expectToast(page, '开始为 multitask_cls_v2:v2 生成向量', 'info')
  const progress = panel.getByRole('region', { name: '生成向量进度' })
  await expect(progress).toBeVisible()
  await expect(progress).toContainText('已完成', { timeout: 15_000 })
  await expectToast(page, '已为 multitask_cls_v2:v2 生成 6 张图片的向量。', 'success')
  await expect(versionRow(panel, 'multitask_cls_v2:v2')).toContainText('可切换')

  // 3. switch: the previous version stays for a roll back
  await expect(guide).toContainText('向量已经齐全，可以切换')
  await guide.getByRole('button', { name: '切换到 multitask_cls_v2:v2' }).click()
  const activate = page.getByRole('dialog', { name: '切换模型版本' })
  await expect(activate).toContainText('原来的 dino:v1 保留')
  await activate.getByRole('button', { name: '切换到 multitask_cls_v2:v2' }).click()
  await expectToast(page, '检索和入库已改用 multitask_cls_v2:v2。dino:v1 保留，可随时切回。', 'success')
  await expect(panel.locator('.mg-metric').filter({ hasText: '检索在用' })).toContainText('multitask_cls_v2:v2')
  await expect(guide).toContainText('检索在用的就是配置的模型')
  await expect(versionRow(panel, 'dino:v1')).toContainText('可切换')

  // roll back, and forward again
  await versionRow(panel, 'dino:v1').getByRole('button', { name: '切回这个版本' }).click()
  const back = page.getByRole('dialog', { name: '切回这个版本' })
  await expect(back).toContainText('按记录的权重')
  await back.getByRole('button', { name: '切换到 dino:v1' }).click()
  await expectToast(page, '检索和入库已改用 dino:v1。', 'success')
  await versionRow(panel, 'multitask_cls_v2:v2').getByRole('button', { name: '切回这个版本' }).click()
  await page.getByRole('dialog', { name: '切回这个版本' }).getByRole('button', { name: '切换到 multitask_cls_v2:v2' }).click()
  await expectToast(page, '检索和入库已改用 multitask_cls_v2:v2。', 'success')

  // retire the old version: its collection goes
  await versionRow(panel, 'dino:v1').getByRole('button', { name: '下线' }).click()
  const retire = page.getByRole('dialog', { name: '下线模型版本' })
  await expect(retire).toContainText('dino_v1')
  await retire.getByRole('button', { name: '下线并删除向量' }).click()
  await expectToast(page, 'dino:v1 已下线，删除了 6 条写入记录和它的向量库。', 'success')
  await expect(versionRow(panel, 'dino:v1')).toHaveCount(0)
  await panel.getByText('已下线的版本（1）').click()
  await expect(panel.getByRole('listitem', { name: 'dino:v1' })).toContainText('向量库已删除')
  expect(calls.filter(call => call.startsWith('POST'))).toEqual([
    'POST /api/embedding-models', 'POST /api/embedding-models/2/build', 'POST /api/embedding-models/2/activate',
    'POST /api/embedding-models/1/activate', 'POST /api/embedding-models/2/activate', 'POST /api/embedding-models/1/retire',
  ])
})

test('有图片还没有新版本的向量时，切换要先确认，并说明它们会暂时检索不到', async ({ page }) => {
  const { rows } = await mockApi(page)
  const panel = await openModels(page)
  await panel.locator('.model-guide').getByRole('button', { name: '登记为新版本' }).click()
  await page.getByRole('dialog', { name: '登记新的模型版本' }).getByRole('button', { name: '登记' }).click()
  await expectToast(page, '已登记', 'success')
  rows[1].embedded = 4  // two originals could not be read
  rows[1].status = 'building'
  await panel.getByRole('button', { name: '刷新' }).click()
  await versionRow(panel, rows[1].name + ':' + rows[1].version).getByRole('button', { name: '切换为当前' }).click()
  const dialog = page.getByRole('dialog', { name: '切换模型版本' })
  await expect(dialog).toContainText('还有 2 / 6 张图片没有这个版本的向量')
  const confirm = dialog.getByRole('button', { name: /^切换到/ })
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel('我知道，仍然切换').check()
  await confirm.click()
  await expectToast(page, '检索和入库已改用', 'success')
  const current = versionRow(panel, rows[1].name + ':' + rows[1].version)
  await expect(current).toContainText('2 张没有')
  await expect(current.getByRole('button', { name: '补齐缺少的向量' })).toBeVisible()
})

test('检索因为编码器和向量库对不上而暂停时，页面说明原因并给出换模型的路', async ({ page }) => {
  await mockApi(page, { stalled: true })
  const panel = await openModels(page)
  await expect(panel.locator('.mg-metric').filter({ hasText: '检索在用' })).toContainText('不可用')
  const guide = panel.locator('.model-guide')
  await expect(guide).toHaveClass(/is-error/)
  await expect(guide).toContainText('检索已暂停：配置的模型和当前版本的向量不是同一个模型，不能混用。')
  await expect(guide.getByRole('button', { name: '登记为新版本' })).toBeVisible()
  await expect(versionRow(panel, 'dino:v1').getByRole('button', { name: '重新启用检索' })).toBeVisible()
})

test('切换被拒时弹窗写明原因和追踪号，检索保持原样', async ({ page }) => {
  const { rows } = await mockApi(page)
  await page.route(url => url.pathname === '/api/embedding-models/2/activate', route => route.fulfill({
    status: 409, json: { ok: false, error: 'EMBEDDING_ENCODER_UNAVAILABLE', detail: 'weights gone', trace_id: 'e2e-trace' },
  }))
  const panel = await openModels(page)
  await panel.locator('.model-guide').getByRole('button', { name: '登记为新版本' }).click()
  await page.getByRole('dialog', { name: '登记新的模型版本' }).getByRole('button', { name: '登记' }).click()
  await expectToast(page, '已登记', 'success')
  rows[1].embedded = IMAGES  // built elsewhere (a worker)
  await panel.getByRole('button', { name: '刷新' }).click()
  const label = `${rows[1].name}:${rows[1].version}`
  await versionRow(panel, label).getByRole('button', { name: '切换为当前' }).click()
  await page.getByRole('dialog', { name: '切换模型版本' }).getByRole('button', { name: `切换到 ${label}` }).click()
  await acknowledgeNotice(page, /编码器没有加载.*weights gone.*追踪号：e2e-trace/, 'error')
  await expect(page.getByRole('dialog', { name: '切换模型版本' })).toBeVisible()  // still open: it can be tried again or cancelled
  await page.getByRole('dialog', { name: '切换模型版本' }).getByRole('button', { name: '取消' }).click()
  await expect(panel.locator('.mg-metric').filter({ hasText: '检索在用' })).toContainText('dino:v1')
})
