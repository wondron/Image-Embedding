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
// 业务搜图's 菜谱识别: two of the three boxes kept; the dish passes 0.95, the plate's closest dish does not
const RECIPE = {
  ok: true, filename: 'dish.png', width: 160, height: 90, caption: 'food . dish', box_threshold: 0.4, text_threshold: 0.4,
  max_regions: 3, threshold: 0.95, top_k: 5, detected: 3, latency_ms: 640.2, classify_ms: 48.6,
  regions: [
    { label: 'food', score: 0.88, box: [40, 20, 120, 70], accepted: true, candidates: [{ label: '荷塘小炒', score: 0.9712 }, { label: '清蒸鲈鱼', score: 0.9031 }] },
    { label: 'dish', score: 0.52, box: [10, 5, 150, 85], accepted: false, candidates: [{ label: '玉米面', score: 0.924 }, { label: '荷塘小炒', score: 0.91 }] },
  ],
  names: ['荷塘小炒'],
  whole_image: [{ label: '玉米面', score: 0.9 }, { label: '荷塘小炒', score: 0.88 }],
}

interface ToolCall { url: URL; multipart: boolean; filename: boolean }
type Answer = { status: number; json: unknown }

/** recipe: one answer for every call, or one per call in turn (the last one repeats). */
async function mockApi(page: Page, gdino: Answer = { status: 200, json: DETECTIONS }, recipe: Answer | Answer[] = { status: 200, json: RECIPE }) {
  const calls: ToolCall[] = []
  const recipeAnswers = Array.isArray(recipe) ? [...recipe] : [recipe]
  const answers: Record<string, () => Answer> = {
    '/api/tools/gdino': () => gdino,
    '/api/tools/segment': () => ({ status: 200, json: SEGMENTATION }),
    '/api/tools/recipe': () => (recipeAnswers.length > 1 ? recipeAnswers.shift()! : recipeAnswers[0]),
  }
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
    if (url.pathname === '/api/enums') return route.fulfill({ json: { ok: true, enums: [] } })
    if (url.pathname === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: [], default_scene: 'general' } })
    const answer = answers[url.pathname]?.()
    if (!answer) return route.abort('blockedbyclient')
    calls.push({
      url,
      multipart: (request.headers()['content-type'] ?? '').startsWith('multipart/form-data'),
      filename: (request.postDataBuffer()?.toString('latin1') ?? '').includes('name="file"; filename="dish.png"'),
    })
    return route.fulfill({ status: answer.status, json: answer.json })
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

test('菜谱识别：按业务搜图的做法识别每个检测框，标出业务搜图会返回的菜名；阈值在浏览器里试', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/?view=tools&tool=recipe')
  const tools = page.getByRole('region', { name: '模型工具' })
  const controls = tools.getByRole('region', { name: '调用参数' })
  await expect(controls.getByRole('button', { name: '菜谱识别' })).toHaveAttribute('aria-pressed', 'true')
  await expect(controls).toContainText('AUTO_SEARCH_REGION_CONFIG.caption')
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles(query)
  await controls.getByLabel('检测什么').fill('food, dish')
  await controls.getByLabel('max_regions').fill('25')
  await expect(controls.getByRole('alert')).toHaveText('max_regions 要是 1～20 的整数。')
  await expect(controls.getByRole('button', { name: '开始识别' })).toBeDisabled()
  await controls.getByLabel('max_regions').fill('3')
  await controls.getByRole('button', { name: '开始识别' }).click()

  const result = tools.getByRole('region', { name: '识别结果' })
  await expect(result.getByRole('table', { name: '每个检测框的识别结果' }).getByRole('row')).toHaveCount(3)
  // the parameters typed, the rest left to AUTO_SEARCH_REGION_CONFIG; the threshold is not sent
  expect(calls).toHaveLength(1)
  expect(calls[0].url.pathname).toBe('/api/tools/recipe')
  expect(calls[0]).toMatchObject({ multipart: true, filename: true })
  expect(calls[0].url.searchParams.getAll('text_range')).toEqual(['food . dish'])
  expect(calls[0].url.searchParams.get('max_regions')).toBe('3')
  expect(calls[0].url.searchParams.get('top_k')).toBe('5')
  for (const name of ['box_threshold', 'text_threshold', 'threshold']) expect(calls[0].url.searchParams.has(name)).toBe(false)

  // at the configured threshold: the dish is returned, the plate is not
  const summary = result.locator('.mt-summary')
  await expect(summary).toContainText('荷塘小炒')
  await expect(summary).toContainText('2（检测到 3 个）')
  const outcome = result.locator('.rt-outcome')
  await expect(outcome).toHaveClass(/is-accepted/)
  await expect(outcome.locator('.rt-name')).toHaveText(['荷塘小炒'])
  const rects = result.locator('svg.rt-boxes rect')
  await expect(rects).toHaveCount(2)
  await expect(rects.nth(0)).not.toHaveClass(/is-rejected/)
  await expect(rects.nth(1)).toHaveClass(/is-rejected/)
  await expect(result.locator('.mt-box-label')).toHaveText(['#1 荷塘小炒 0.971', '#2 玉米面 0.924'])
  const rows = result.getByRole('table', { name: '每个检测框的识别结果' }).locator('tbody tr')
  await expect(rows.nth(0)).toContainText('会返回')
  await expect(rows.nth(0)).toContainText('清蒸鲈鱼 0.903')
  await expect(rows.nth(1)).toContainText('低于阈值')
  await expect(rows.nth(1)).toContainText('−0.0260')
  // what the model was given: the box's crop, letterboxed
  await expect(result.getByRole('img', { name: '#1 检测框的裁剪图（模型的输入）' }).locator('svg')).toHaveAttribute('viewBox', '40 20 80 50')
  // a row highlights its box
  await rows.nth(1).hover()
  await expect(rects.nth(1)).toHaveClass(/is-active/)
  await expect(rects.nth(0)).toHaveClass(/is-dim/)
  // the whole image, for comparison
  const whole = result.getByRole('region', { name: '整张图的识别结果' })
  await expect(whole).toContainText('玉米面')
  await expect(whole).toContainText('0.9000')

  // a lower threshold takes the plate's dish too; a higher one takes nothing — without calling again
  const threshold = result.getByLabel('菜谱阈值的数值')
  await expect(threshold).toHaveValue('0.95')
  await threshold.fill('0.92')
  await expect(outcome.locator('.rt-name')).toHaveText(['荷塘小炒', '玉米面'])
  await expect(rects.nth(1)).not.toHaveClass(/is-rejected/)
  await expect(summary).toContainText('荷塘小炒、玉米面')
  await threshold.fill('0.98')
  await expect(outcome).not.toHaveClass(/is-accepted/)
  await expect(outcome).toContainText('没有框过阈值 0.98，业务搜图不会给出菜谱卡。')
  await expect(outcome).toContainText('最接近的是 #1 的「荷塘小炒」0.9712，还差 0.0088。')
  await expect(rects.nth(0)).toHaveClass(/is-rejected/)
  // the slider moves the same threshold
  const slider = result.getByLabel('菜谱阈值', { exact: true })
  await slider.focus()
  await page.keyboard.press('ArrowLeft')
  await expect(threshold).toHaveValue('0.979')
  await result.getByRole('button', { name: '恢复配置值 0.95' }).click()
  await expect(threshold).toHaveValue('0.95')
  await expect(outcome.locator('.rt-name')).toHaveText(['荷塘小炒'])
  await expect(result.getByRole('button', { name: /恢复配置值/ })).toHaveCount(0)
  expect(calls).toHaveLength(1)
})

test('菜谱识别没有开启时说明原因；没有可识别的框时说业务搜图不会给出菜谱卡', async ({ page }) => {
  await mockApi(page, undefined, [
    { status: 503, json: { ok: false, error: 'NOT_READY', detail: 'recipe model is disabled or not initialized', trace_id: 'e2e-recipe' } },
    { status: 200, json: { ...RECIPE, detected: 2, regions: [], names: [] } },
  ])
  await page.goto('/?view=tools&tool=recipe')
  const tools = page.getByRole('region', { name: '模型工具' })
  const controls = tools.getByRole('region', { name: '调用参数' })
  await controls.getByLabel('选择图片', { exact: true }).setInputFiles(query)
  await controls.getByRole('button', { name: '开始识别' }).click()
  await acknowledgeNotice(page, 'recipe model is disabled or not initialized', 'error')
  await expect(tools.getByRole('region', { name: '识别结果' })).toContainText('还没有菜谱识别结果')

  // GroundingDINO found boxes, all too small to use: no recipe card, whatever the whole image looks like
  await controls.getByRole('button', { name: '开始识别' }).click()
  const result = tools.getByRole('region', { name: '识别结果' })
  await expect(result.locator('.rt-outcome')).toContainText('没有可识别的检测框，业务搜图不会给出菜谱卡')
  await expect(result.locator('.rt-outcome')).toContainText('GroundingDINO 给了 2 个框，都因边长小于 16 像素被丢掉。')
  await expect(result.getByRole('table')).toHaveCount(0)
  await expect(result.getByRole('region', { name: '整张图的识别结果' })).toContainText('玉米面')
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
