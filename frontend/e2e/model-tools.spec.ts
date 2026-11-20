import { crc32, deflateSync } from 'node:zlib'
import { expect, test, type Page } from '@playwright/test'
import { acknowledgeNotice } from './notice'

// Fixtures only: /api/tools/* answer from here; nothing reaches a real backend.

/** An 8-bit PNG of width × height: `pixel` gives each pixel's gray byte (1 channel) or RGB (3). */
function png(width: number, height: number, channels: 1 | 3, pixel: (x: number, y: number) => readonly number[]): Buffer {
  const stride = width * channels + 1 // each row starts with filter type 0
  const raw = Buffer.alloc(stride * height)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) raw.set(pixel(x, y), y * stride + 1 + x * channels)
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(body))
    return Buffer.concat([length, body, crc])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8 // bit depth
  header[9] = channels === 1 ? 0 : 2 // grayscale / RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ])
}

// A 160 × 90 image: an orange dish (x 40–119, y 20–69) on gray. Its mask: class 1 on the dish (27.8 %).
const onDish = (x: number, y: number) => x >= 40 && x < 120 && y >= 20 && y < 70
const QUERY = png(160, 90, 3, (x, y) => (onDish(x, y) ? [234, 128, 52] : [226, 230, 236]))
const MASK = png(160, 90, 1, (x, y) => [onDish(x, y) ? 1 : 0]).toString('base64')
const query = { name: 'dish.png', mimeType: 'image/png', buffer: QUERY }

const DETECTIONS = {
  ok: true, filename: 'dish.png', width: 160, height: 90, caption: 'food . plate', box_threshold: 0.3, text_threshold: 0.25, latency_ms: 812.4,
  detections: [
    { label: 'food', score: 0.88, box: [40, 20, 120, 70] },
    { label: 'plate', score: 0.52, box: [10, 5, 150, 85] },
  ],
}
const SEGMENTATION = { ok: true, filename: 'dish.png', width: 160, height: 90, latency_ms: 356.2, mask_png_base64: MASK, visual_png_base64: null }

interface ToolCall { url: URL; multipart: boolean; filename: boolean }

async function mockApi(page: Page, gdino: { status: number; json: unknown } = { status: 200, json: DETECTIONS }) {
  const calls: ToolCall[] = []
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (url.pathname === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (url.pathname === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    if (url.pathname !== '/api/tools/gdino' && url.pathname !== '/api/tools/segment') return route.abort('blockedbyclient')
    calls.push({
      url,
      multipart: (request.headers()['content-type'] ?? '').startsWith('multipart/form-data'),
      filename: (request.postDataBuffer()?.toString('latin1') ?? '').includes('name="file"; filename="dish.png"'),
    })
    return url.pathname === '/api/tools/gdino'
      ? route.fulfill({ status: gdino.status, json: gdino.json })
      : route.fulfill({ json: SEGMENTATION })
  })
  return calls
}

async function openTools(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '模型工具' }).click()
  return page.getByRole('region', { name: '模型工具' })
}

/** RGBA of one pixel of the canvas called `label`, in the image's pixels. */
function pixel(page: Page, label: string, x: number, y: number) {
  return page.evaluate(([name, px, py]) => {
    const canvas = document.querySelector<HTMLCanvasElement>(`canvas[aria-label="${name}"]`)
    return canvas ? Array.from(canvas.getContext('2d')!.getImageData(px, py, 1, 1).data) : null
  }, [label, x, y] as const)
}

test('目标检测：模型工具在图搜图下面；按 caption 和阈值调用 GroundingDINO，框画在原图上', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/')
  const items = page.getByRole('navigation', { name: '主导航' }).locator('.nav-item')
  await expect(items.nth(0)).toHaveAccessibleName('图搜图')
  await expect(items.nth(1)).toHaveAccessibleName('模型工具')
  const tools = await openTools(page)
  await expect(page).toHaveURL(/[?&]view=tools(&|$)/)
  await expect(page).toHaveURL(/[?&]tool=gdino(&|$)/)

  const controls = tools.getByRole('region', { name: '调用参数' })
  await expect(controls.getByRole('button', { name: '开始检测' })).toBeDisabled()
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles(query)
  await controls.getByLabel('检测什么').fill('food, plate')
  await controls.getByLabel('box_threshold').fill('0.3')
  await controls.getByRole('button', { name: '开始检测' }).click()

  const result = tools.getByRole('region', { name: '检测结果' })
  await expect(result.getByRole('table', { name: '检测框' }).getByRole('row')).toHaveCount(3)
  // one caption in GroundingDINO's form, the threshold typed, text_threshold left to GROUNDINGDINO_CONFIG
  expect(calls).toHaveLength(1)
  expect(calls[0]).toMatchObject({ multipart: true, filename: true })
  expect(calls[0].url.searchParams.getAll('text_range')).toEqual(['food . plate'])
  expect(calls[0].url.searchParams.get('box_threshold')).toBe('0.3')
  expect(calls[0].url.searchParams.has('text_threshold')).toBe(false)
  // what was used, as the response says
  const summary = result.locator('.mt-summary')
  await expect(summary).toContainText('food . plate')
  await expect(summary).toContainText('0.25')
  await expect(summary).toContainText('160 × 90')

  // boxes in the image's pixels; a label at the top edge goes inside its box
  const rects = result.locator('svg.mt-boxes rect')
  await expect(rects).toHaveCount(2)
  await expect(rects.first()).toHaveAttribute('x', '40')
  await expect(rects.first()).toHaveAttribute('width', '80')
  await expect(rects.first()).toHaveAttribute('height', '50')
  const labels = result.locator('.mt-box-label')
  await expect(labels).toHaveText(['food 0.88', 'plate 0.52'])
  await expect(labels.nth(0)).not.toHaveClass(/is-inside/)
  await expect(labels.nth(1)).toHaveClass(/is-inside/)
  // a row highlights its box
  await result.getByRole('row', { name: /plate/ }).hover()
  await expect(rects.nth(1)).toHaveClass(/is-active/)
  await expect(rects.nth(0)).toHaveClass(/is-dim/)

  // another image: the results of this one go
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles({ ...query, name: 'other.png' })
  await expect(result).toContainText('还没有目标检测结果')
  // leaving the page drops its parameters from the URL
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '图搜图' }).click()
  await expect(page).not.toHaveURL(/[?&](view|tool)=/)
})

test('图像分割：只要 mask（output_vis=false），在浏览器里叠加、描轮廓、抠图和并排', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/?view=tools&tool=segment')
  const tools = page.getByRole('region', { name: '模型工具' })
  const controls = tools.getByRole('region', { name: '调用参数' })
  await expect(controls.getByRole('button', { name: '图像分割' })).toHaveAttribute('aria-pressed', 'true')
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles(query)
  await controls.getByRole('button', { name: '开始分割' }).click()

  const result = tools.getByRole('region', { name: '分割结果' })
  await expect(result.locator('.mt-summary')).toContainText('27.8%')
  await expect(result.getByRole('list', { name: 'mask 里的类别' })).toContainText('类别 1')
  expect(calls).toHaveLength(1)
  expect(calls[0].url.pathname).toBe('/api/tools/segment')
  expect(calls[0].url.searchParams.get('output_vis')).toBe('false')
  expect(calls[0]).toMatchObject({ multipart: true, filename: true })

  // 叠加: the dish in the overlay colour, the rest transparent, the layer at the chosen opacity
  await expect.poll(() => pixel(page, '叠加的 mask', 80, 45)).toEqual([34, 197, 94, 255])
  expect(await pixel(page, '叠加的 mask', 5, 5)).toEqual([0, 0, 0, 0])
  const overlay = result.getByRole('img', { name: '叠加的 mask' })
  await expect(overlay).toHaveCSS('opacity', '0.5')
  await result.getByLabel('不透明度').fill('0.8')
  await expect(overlay).toHaveCSS('opacity', '0.8')
  await result.getByLabel('颜色').fill('#ff0000')
  await expect.poll(() => pixel(page, '叠加的 mask', 80, 45)).toEqual([255, 0, 0, 255])

  // 轮廓: the dish's edge, not its inside
  await result.getByRole('button', { name: '轮廓' }).click()
  await expect.poll(() => pixel(page, 'mask 的轮廓', 40, 45)).toEqual([255, 0, 0, 255])
  expect(await pixel(page, 'mask 的轮廓', 80, 45)).toEqual([0, 0, 0, 0])

  // 抠图: the dish's own pixels, nothing around it
  await result.getByRole('button', { name: '抠图' }).click()
  await expect.poll(() => pixel(page, '抠出的前景', 80, 45)).toEqual([234, 128, 52, 255])
  expect(await pixel(page, '抠出的前景', 5, 5)).toEqual([0, 0, 0, 0])

  // 并排: the original next to the mask on its own
  await result.getByRole('button', { name: '并排' }).click()
  await expect(result.getByRole('img', { name: '原图' })).toBeVisible()
  await expect.poll(() => pixel(page, 'mask', 5, 5)).toEqual([15, 23, 42, 255])
  expect(await pixel(page, 'mask', 80, 45)).toEqual([255, 0, 0, 255])

  // the views are drawn here: the server was called once
  expect(calls).toHaveLength(1)
})

test('阈值超出 0～1 时不能调用；模型没有加载时说明原因', async ({ page }) => {
  await mockApi(page, { status: 503, json: { ok: false, error: 'NOT_READY', detail: 'GroundingDINO is disabled or not initialized', trace_id: 'e2e-gdino' } })
  const tools = await openTools(page)
  const controls = tools.getByRole('region', { name: '调用参数' })
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles(query)

  await controls.getByLabel('text_threshold').fill('1.5')
  await expect(controls.getByRole('alert')).toHaveText('阈值要在 0～1 之间。')
  await expect(controls.getByRole('button', { name: '开始检测' })).toBeDisabled()
  await controls.getByLabel('text_threshold').fill('')
  await expect(controls.getByRole('alert')).toHaveCount(0)

  await controls.getByRole('button', { name: '开始检测' }).click()
  await acknowledgeNotice(page, 'GroundingDINO is disabled or not initialized', 'error')
  await expect(tools.getByRole('region', { name: '检测结果' })).toContainText('还没有目标检测结果')
  await expect(controls.getByRole('button', { name: '开始检测' })).toBeEnabled()
})
