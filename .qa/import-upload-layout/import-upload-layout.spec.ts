/**
 * Independent I1–I9 acceptance: real window measurements + screenshots.
 * Not an implementer source-mapping pass. QA only under .qa/import-upload-layout/.
 */
import { test, expect, type Page, type Locator } from '../../frontend/node_modules/@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const OUT = path.dirname(__filename)
const SHOTS = path.join(OUT, 'screenshots')
const MEASURE = path.join(OUT, 'measurements.json')

type Box = { x: number; y: number; w: number; h: number; top: number; bottom: number; left: number; right: number }
type Verdict = 'PASS' | 'FAIL' | 'SKIP' | 'BLOCKED'

const RESULTS: Record<string, any> = {
  recorded_at: null,
  points: {},
  screenshots: [],
  notes: [],
}

function pngBytes(): Buffer {
  return Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
    'base64',
  )
}

function recordShot(name: string) {
  RESULTS.screenshots.push(name)
  return path.join(SHOTS, name)
}

async function boxOf(loc: Locator): Promise<Box | null> {
  const el = loc.first()
  if (await el.count() === 0) return null
  const h = await el.elementHandle()
  if (!h) return null
  const b = await h.boundingBox()
  if (!b) return null
  return { x: b.x, y: b.y, w: b.width, h: b.height, top: b.y, bottom: b.y + b.height, left: b.x, right: b.x + b.width }
}

async function metrics(page: Page) {
  return page.evaluate(() => {
    const html = document.documentElement
    const body = document.body
    const main = document.querySelector('main') as HTMLElement | null
    const pageEl = document.querySelector('.mg-page') as HTMLElement | null
    const left = document.querySelector('.mg-import-main') as HTMLElement | null
    const aside = document.querySelector('.mg-import-aside') as HTMLElement | null
    const form = document.querySelector('.mg-import-settings-body') as HTMLElement | null
    const meta = document.querySelector('.mg-import-meta') as HTMLElement | null
    const pickup = document.querySelector('.mg-pickup-row') as HTMLElement | null
    const run = document.querySelector('.mg-import-run') as HTMLElement | null
    const drop = document.querySelector('.mg-pickup-row .mg-dropzone') as HTMLElement | null
    const cs = getComputedStyle(document.documentElement)
    const rem = parseFloat(cs.fontSize || '16')
    const topbar = parseFloat((cs.getPropertyValue('--app-topbar-h') || '56').replace('px', '')) || 56
    const primary = cs.getPropertyValue('--primary').trim()
    const sidebarW = cs.getPropertyValue('--sidebar-width').trim()
    const box = (el: HTMLElement | null) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      const s = getComputedStyle(el)
      return {
        x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, left: r.left, right: r.right,
        scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
        overflow: s.overflow, overflowX: s.overflowX, overflowY: s.overflowY,
        position: s.position, topCss: s.top, bottomCss: s.bottom, maxHeight: s.maxHeight, height: s.height,
        className: el.className,
      }
    }
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio },
      dvh: (window as any).innerHeight,
      rem,
      topbar,
      primary,
      sidebarW,
      estimate75rem: 7.5 * rem,
      windowScroll: { x: window.scrollX, y: window.scrollY, maxY: html.scrollHeight - html.clientHeight },
      html: { scrollHeight: html.scrollHeight, clientHeight: html.clientHeight, overflow: getComputedStyle(html).overflow },
      body: { scrollHeight: body.scrollHeight, clientHeight: body.clientHeight, overflow: getComputedStyle(body).overflow },
      main: box(main),
      page: box(pageEl),
      left: box(left),
      aside: box(aside),
      form: box(form),
      meta: box(meta),
      pickup: box(pickup),
      run: box(run),
      drop: box(drop),
      mainClass: main?.className ?? '',
      pageClass: pageEl?.className ?? '',
    }
  })
}

function fullyIn(inner: Box, outer: Box, eps = 1.5) {
  return inner.top >= outer.top - eps && inner.bottom <= outer.bottom + eps && inner.left >= outer.left - eps && inner.right <= outer.right + eps
}

function verticallyOverlaps(a: Box, b: Box, min = 8) {
  return Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) >= min
}

function sameRow(a: Box, b: Box) {
  // Stretch row: midpoints close, or vertical ranges overlap and b is not clearly a new row.
  const midDelta = Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2)
  const newRow = b.top >= a.bottom - 2 && b.top > a.top + Math.min(a.h, 24)
  return !newRow && (verticallyOverlaps(a, b, 4) || midDelta <= Math.max(a.h, b.h) / 2 + 4)
}

async function visibleClickable(page: Page, loc: Locator) {
  const b = await boxOf(loc)
  if (!b || b.w < 2 || b.h < 2) return { ok: false, reason: 'no box', box: b, disabled: null, hit: null }
  const disabled = await loc.first().isDisabled().catch(() => false)
  const cx = b.x + b.w / 2
  const cy = b.y + b.h / 2
  const hit = await page.evaluate(({ x, y }) => {
    const el = document.elementFromPoint(x, y) as HTMLElement | null
    if (!el) return null
    return { tag: el.tagName, className: el.className, id: el.id, text: (el.innerText || '').slice(0, 80) }
  }, { x: cx, y: cy })
  const handle = await loc.first().elementHandle()
  const covers = hit && handle ? await page.evaluate(({ hx, el }) => {
    const node = document.elementFromPoint(hx.x, hx.y)
    return !!(node && el.contains(node) || (node && el === node))
  }, { hx: { x: cx, y: cy }, el: handle }).catch(() => false) : false
  return { ok: covers, reason: covers ? 'hit' : 'covered-or-miss', box: b, disabled, hit, point: { x: cx, y: cy } }
}

async function gotoImport(page: Page) {
  // App.initialView() only honors ?view=enums|categories|scenes and otherwise
  // opens search. Import is reached through the sidebar, which is the real path.
  // At ≤760px the sidebar is off-canvas, so widen first, click, then restore.
  const desired = page.viewportSize()
  const narrow = Boolean(desired && desired.width <= 760)
  if (narrow) await page.setViewportSize({ width: 1440, height: desired!.height })
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.app-shell', { timeout: 30_000 })
  const nav = page.getByRole('button', { name: '上传与导入' })
  await nav.click()
  await page.waitForSelector('.mg-page-import', { timeout: 20_000 })
  if (narrow && desired) await page.setViewportSize(desired)
  await page.waitForTimeout(250)
}

async function fillQueue(page: Page, count: number) {
  const files = Array.from({ length: count }, (_, i) => ({
    name: `qa-import-layout-${String(i).padStart(3, '0')}.png`,
    mimeType: 'image/png',
    buffer: pngBytes(),
  }))
  const input = page.locator('input[aria-label="选择待入库图片"]')
  await input.setInputFiles(files)
  await page.waitForFunction((n) => {
    const el = document.querySelector('.mg-queue-heading h3 span')
    return el && parseInt(el.textContent || '0', 10) >= n
  }, count, { timeout: 20_000 })
}

async function setIndexingState(page: Page, value: boolean) {
  return page.evaluate((value) => {
    const el = document.querySelector('.mg-page-import')
    if (!el) throw new Error('no .mg-page-import')
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'))
    if (!key) throw new Error('no react fiber key')
    let fiber: any = (el as any)[key]
    while (fiber && fiber.type?.name !== 'ManagementViews' && fiber.elementType?.name !== 'ManagementViews') {
      fiber = fiber.return
    }
    if (!fiber) throw new Error('ManagementViews fiber not found')
    const states: any[] = []
    const refs: any[] = []
    let hook = fiber.memoizedState
    while (hook) {
      const q = hook.queue
      const m = hook.memoizedState
      if (q && typeof q.dispatch === 'function') states.push(hook)
      else if (m && typeof m === 'object' && 'current' in m) refs.push(m)
      hook = hook.next
    }
    if (states.length < 9) throw new Error(`unexpected useState count ${states.length}`)
    // index 8 is `indexing`
    states[8].queue.dispatch(value)
    const boolRefs = refs.filter(r => typeof r.current === 'boolean')
    for (const r of boolRefs) r.current = value
    return { useStateCount: states.length, boolRefsSet: boolRefs.length }
  }, value)
}

test.describe.configure({ mode: 'default' })

test.beforeAll(() => {
  fs.mkdirSync(SHOTS, { recursive: true })
})

test.afterAll(() => {
  RESULTS.recorded_at = new Date().toISOString()
  fs.writeFileSync(MEASURE, JSON.stringify(RESULTS, null, 2), 'utf8')
})

test('I1 wide empty queue: pickup on one row, meta above', async ({ page }) => {
  const id = 'I1'
  const fails: string[] = []
  await page.setViewportSize({ width: 1440, height: 1000 })
  await gotoImport(page)
  const choose = page.getByRole('button', { name: '选择图片' })
  const dirBtn = page.getByRole('button', { name: '选择目录' })
  const dirFallback = page.getByText('目录选择不可用')
  const drop = page.locator('.mg-pickup-row .mg-dropzone')
  const meta = page.locator('.mg-import-meta')
  const status = meta.locator('.mg-status')
  const format = meta.getByText(/JPG、PNG、WebP/)
  await expect(choose).toBeVisible()
  await expect(drop).toBeVisible()
  await expect(status).toHaveText(/自建入库已接入/)
  await expect(format).toBeVisible()
  const dirAvailable = await dirBtn.count() > 0
  const second = dirAvailable ? dirBtn : dirFallback
  await expect(second).toBeVisible()

  const bChoose = await boxOf(choose)
  const bSecond = await boxOf(second)
  const bDrop = await boxOf(drop)
  const bMeta = await boxOf(meta)
  const bStatus = await boxOf(status)
  const bFormat = await boxOf(format)
  const bPickup = await boxOf(page.locator('.mg-pickup-row'))
  const m = await metrics(page)
  await page.screenshot({ path: recordShot('I1-wide-empty.png'), fullPage: false })

  if (!bChoose || !bSecond || !bDrop || !bMeta || !bStatus || !bFormat || !bPickup) {
    fails.push('missing geometry for pickup or meta')
  } else {
    if (!sameRow(bChoose, bSecond)) fails.push('two chooser controls are not on the same row')
    if (!sameRow(bChoose, bDrop) || !sameRow(bSecond, bDrop)) fails.push('dropzone is not on the same row as the chooser buttons')
    if (bDrop.left < bSecond.right - 2 && bDrop.left < bChoose.right - 2) {
      // dropzone should sit to the right of the buttons in LTR
      if (bDrop.left + 4 < Math.max(bChoose.right, bSecond.right)) {
        // only fail if it is clearly not to the right; wrap would already fail sameRow
      }
    }
    if (bDrop.left + 4 < Math.max(bChoose.right, bSecond.right) && sameRow(bChoose, bDrop)) {
      fails.push('dropzone is not to the right of the chooser buttons')
    }
    if (!(bMeta.bottom <= bPickup.top + 6)) fails.push('meta is not above the pickup row')
    if (sameRow(bStatus, bChoose) && verticallyOverlaps(bStatus, bPickup, 8) && bStatus.top >= bPickup.top - 4) {
      fails.push('status chip appears to sit in the pickup row')
    }
    if (sameRow(bFormat, bChoose) && bFormat.top >= bPickup.top - 4 && verticallyOverlaps(bFormat, bPickup, 8)) {
      fails.push('format hint appears to sit in the pickup row')
    }
    // Status / format must be inside meta, not pickup
    const statusInPickup = fullyIn(bStatus, bPickup, 4)
    const formatInPickup = fullyIn(bFormat, bPickup, 4)
    if (statusInPickup) fails.push('status chip is geometrically inside the pickup row')
    if (formatInPickup) fails.push('format hint is geometrically inside the pickup row')
  }
  const hint = await drop.locator('p').innerText().catch(() => '')
  if (/顶部/.test(hint)) fails.push(`dropzone hint still says 顶部: ${hint}`)

  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, viewport: m.viewport, dirAvailable, fails, boxes: { bChoose, bSecond, bDrop, bMeta, bStatus, bFormat, bPickup }, metrics: m, hint }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I2 wide long queue: left column scrolls, right rail stays, run pinned', async ({ page }) => {
  const id = 'I2'
  const fails: string[] = []
  await page.setViewportSize({ width: 1440, height: 1000 })
  await gotoImport(page)
  await fillQueue(page, 40)
  // If the form body is short, stretch it so we can verify form-only scrolling (page style, not worktree).
  const formStyle = await page.addStyleTag({ content: '.mg-import-settings-body .ia-form { min-height: 1400px !important; }' })
  await page.waitForTimeout(150)

  const left = page.locator('.mg-import-main')
  const aside = page.locator('.mg-import-aside')
  const title = aside.locator('.mg-import-settings-head h2')
  const runBtn = page.getByRole('button', { name: /开始入库|继续入库|取消入库/ })
  const form = page.locator('.mg-import-settings-body')

  const before = await metrics(page)
  if (!before.left || before.left.scrollHeight <= before.left.clientHeight + 8) {
    fails.push(`left column did not overflow after 40 items (scrollHeight=${before.left?.scrollHeight}, clientHeight=${before.left?.clientHeight})`)
  }
  await left.evaluate(el => { el.scrollTop = el.scrollHeight })
  await page.waitForTimeout(200)
  const afterLeft = await metrics(page)
  await page.screenshot({ path: recordShot('I2-wide-left-scrolled.png'), fullPage: false })

  const titleBox = await boxOf(title)
  const runBox = await boxOf(runBtn)
  const asideBox = await boxOf(aside)
  const mainBox = afterLeft.main
  if (!titleBox) fails.push('批量标注 title not visible after left scroll')
  else if (mainBox && !fullyIn(titleBox, { x: mainBox.x, y: mainBox.y, w: mainBox.w, h: mainBox.h, top: mainBox.top, bottom: mainBox.bottom, left: mainBox.left, right: mainBox.right } as Box, 3)) {
    fails.push('批量标注 title is not fully inside main after left scroll')
  }
  if (!runBox) fails.push('开始入库 button has no box after left scroll')
  else if (mainBox && !fullyIn(runBox, { x: mainBox.x, y: mainBox.y, w: mainBox.w, h: mainBox.h, top: mainBox.top, bottom: mainBox.bottom, left: mainBox.left, right: mainBox.right } as Box, 3)) {
    fails.push('开始入库 is not fully inside main after left scroll')
  }
  const click = await visibleClickable(page, runBtn)
  if (!click.ok) fails.push(`开始入库 not hit-testable after left scroll: ${click.reason}`)

  // Window / main should not have taken over the left-column scroll
  if (afterLeft.windowScroll.y > 1) fails.push(`window scrolled (y=${afterLeft.windowScroll.y})`)
  if (afterLeft.main && afterLeft.main.scrollTop > 2) fails.push(`main scrolled (scrollTop=${afterLeft.main.scrollTop}); left column should be the queue scroller`)
  if (afterLeft.left && afterLeft.left.scrollTop < 20) fails.push('left column scrollTop did not move')

  // Form-only scroll: run button stays, form scrollTop moves
  const formBefore = afterLeft.form
  if (formBefore && formBefore.scrollHeight > formBefore.clientHeight + 8) {
    await form.evaluate(el => { el.scrollTop = el.scrollHeight })
    await page.waitForTimeout(200)
    const afterForm = await metrics(page)
    await page.screenshot({ path: recordShot('I2-form-scrolled.png'), fullPage: false })
    const runAfter = await boxOf(runBtn)
    if (!runAfter) fails.push('开始入库 disappeared after form scroll')
    else if (runBox && Math.abs(runAfter.top - runBox.top) > 6) fails.push(`开始入库 moved with the form (top ${runBox.top} → ${runAfter.top})`)
    if (afterForm.form && afterForm.form.scrollTop < 20) fails.push('form body scrollTop did not move')
    RESULTS.points[id] = RESULTS.points[id] || {}
    RESULTS.points[id].formScroll = afterForm.form
  } else {
    fails.push('form body did not overflow even after injected min-height (cannot verify form-only scroll)')
  }

  await formStyle.evaluate((el: HTMLElement) => el.remove()).catch(() => {})
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = {
    ...(RESULTS.points[id] || {}),
    verdict, fails, click, before, afterLeft,
    titleBox, runBox, asideBox,
    note: 'Form min-height 1400px was injected via page.addStyleTag only, then removed. Not written to the worktree.',
  }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I3 forced main overflow: sticky rail stays, no leftover styles', async ({ page }) => {
  const id = 'I3'
  const fails: string[] = []
  await page.setViewportSize({ width: 1440, height: 1000 })
  await gotoImport(page)
  await fillQueue(page, 40)
  const style = await page.addStyleTag({
    content: `
      main.import-page, main.management-page { overflow: auto !important; }
      .mg-page-import { height: auto !important; overflow: visible !important; flex: none !important; }
      .mg-page-import .mg-import-layout { height: auto !important; min-height: 0 !important; flex: none !important; }
      .mg-page-import .mg-import-main { overflow: visible !important; height: auto !important; max-height: none !important; }
    `,
  })
  await page.waitForTimeout(200)
  const before = await metrics(page)
  const mainHandle = page.locator('main')
  const canScrollMain = !!(before.main && before.main.scrollHeight > before.main.clientHeight + 20)
  if (!canScrollMain) {
    // also try scrolling the window if main didn't grow
    await mainHandle.evaluate(el => { el.scrollTop = el.scrollHeight })
  } else {
    await mainHandle.evaluate(el => { el.scrollTop = el.scrollHeight })
  }
  await page.waitForTimeout(200)
  const after = await metrics(page)
  await page.screenshot({ path: recordShot('I3-main-forced-scroll.png'), fullPage: false })
  const titleBox = await boxOf(page.locator('.mg-import-settings-head h2'))
  const runBox = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  const mainBox = after.main
  if (!mainBox) fails.push('main box missing')
  if (!titleBox) fails.push('title not visible under forced main scroll')
  else if (mainBox && !fullyIn(titleBox, mainBox as any, 4)) fails.push('title left the main visible area')
  if (!runBox) fails.push('开始入库 not visible under forced main scroll')
  else if (mainBox && !fullyIn(runBox, mainBox as any, 4)) fails.push('开始入库 left the main visible area')
  const click = await visibleClickable(page, page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  if (!click.ok) fails.push(`开始入库 not hit-testable under forced main scroll: ${click.reason}`)
  if (after.aside) {
    if (after.aside.position !== 'sticky' && after.aside.position !== 'fixed') {
      fails.push(`aside position is ${after.aside.position}, expected sticky (or fixed) under 3.2`)
    }
    if (!/dvh|px/.test(after.aside.maxHeight) || after.aside.maxHeight === '100%') {
      fails.push(`aside max-height is ${after.aside.maxHeight}, 3.2 forbids 100%`)
    }
  }
  // Remove the experimental style and confirm it is gone (not in worktree by construction).
  await style.evaluate((el: HTMLElement) => el.remove())
  await page.waitForTimeout(150)
  const leftover = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('style')).some(s => (s.textContent || '').includes('mg-page-import { height: auto'))
  })
  if (leftover) fails.push('experimental style tag was not removed from the page')
  const cssFileUntouched = true // worktree check is done in the runner closeout
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, fails, canScrollMain, before, after, titleBox, runBox, click, leftover, cssFileUntouched }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I4 indexing state via legal frontend injection (no business backend)', async ({ page }) => {
  const id = 'I4'
  const fails: string[] = []
  const notes: string[] = [
    'Evidence boundary: no isolated backend and no real index POST. indexing was toggled via React fiber setState + indexingRef on the live page. This checks meta copy and dropzone disabled class, not a full ingest integration.',
  ]
  await page.setViewportSize({ width: 1440, height: 1000 })
  await gotoImport(page)
  await fillQueue(page, 4)
  const metaStatus = page.locator('.mg-import-meta .mg-status')
  await expect(metaStatus).toHaveText(/自建入库已接入/)
  const injected = await setIndexingState(page, true)
  await page.waitForTimeout(200)
  const text = (await metaStatus.innerText()).replace(/\s+/g, '')
  if (!text.includes('入库进行中')) fails.push(`meta status after injection is ${JSON.stringify(await metaStatus.innerText())}`)
  const inMeta = await metaStatus.evaluate(el => !!el.closest('.mg-import-meta') && !el.closest('.mg-pickup-row'))
  if (!inMeta) fails.push('进行中 status is not in .mg-import-meta (or is inside pickup)')
  const drop = page.locator('.mg-pickup-row .mg-dropzone')
  const dropClass = await drop.getAttribute('class')
  if (!dropClass?.includes('is-disabled')) fails.push(`dropzone class missing is-disabled: ${dropClass}`)
  const pe = await drop.evaluate(el => getComputedStyle(el).pointerEvents)
  if (pe !== 'none') fails.push(`dropzone pointer-events is ${pe}, expected none`)
  const chooseDisabled = await page.getByRole('button', { name: '选择图片' }).isDisabled()
  if (!chooseDisabled) notes.push('选择图片 was not disabled; production also disables it when indexing is true — if injection only flipped the render state this may be a hook-index miss on that control.')
  await page.screenshot({ path: recordShot('I4-indexing-injected.png'), fullPage: false })
  // Revert injection so later tests start clean
  await setIndexingState(page, false)
  await page.waitForTimeout(150)
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, fails, notes, injected, text, dropClass, pointerEvents: pe, chooseDisabled, evidenceBoundary: notes[0] }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I5 <=860px bottom panel stays, form scrolls, 7.5rem occupancy', async ({ page }) => {
  const id = 'I5'
  const fails: string[] = []
  await page.setViewportSize({ width: 860, height: 1000 })
  await gotoImport(page)
  await fillQueue(page, 40)
  const formStyle = await page.addStyleTag({ content: '.mg-import-settings-body .ia-form { min-height: 1400px !important; }' })
  await page.waitForTimeout(200)
  const left = page.locator('.mg-import-main')
  await left.evaluate(el => { el.scrollTop = el.scrollHeight })
  await page.waitForTimeout(200)
  const m = await metrics(page)
  await page.screenshot({ path: recordShot('I5-860-left-scrolled.png'), fullPage: false })

  const titleBox = await boxOf(page.locator('.mg-import-settings-head h2'))
  const runBox = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  const pickupBox = await boxOf(page.locator('.mg-pickup-row'))
  const metaBox = await boxOf(page.locator('.mg-import-meta'))
  const asideBox = await boxOf(page.locator('.mg-import-aside'))
  const mainBox = m.main

  if (!titleBox) fails.push('批量标注 title not visible at 860 after left scroll')
  if (!runBox) fails.push('开始入库 not visible at 860 after left scroll')
  if (titleBox && mainBox && !fullyIn(titleBox, mainBox as any, 4)) fails.push('title left the main visible area at 860')
  if (runBox && mainBox && !fullyIn(runBox, mainBox as any, 4)) fails.push('开始入库 left the main visible area at 860')
  const click = await visibleClickable(page, page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  if (!click.ok) fails.push(`开始入库 not hit-testable at 860: ${click.reason}`)

  // Panel should sit at the bottom of the visible main, not after the long queue in the left flow
  if (asideBox && mainBox && pickupBox) {
    if (asideBox.top < pickupBox.bottom - 2) {
      // overlapping pickup?
    }
    const gapFromMainBottom = Math.abs(asideBox.bottom - (mainBox as any).bottom)
    if (asideBox.top > (mainBox as any).bottom - 40) fails.push('aside is not a bottom-of-main panel')
    if (m.windowScroll.y > 1) fails.push(`window scrolled at 860 (y=${m.windowScroll.y})`)
  }
  if (pickupBox && asideBox) {
    const overlap = Math.min(pickupBox.bottom, asideBox.bottom) - Math.max(pickupBox.top, asideBox.top)
    if (overlap > 4) fails.push(`bottom panel overlaps pickup by ${overlap.toFixed(1)}px (7.5rem estimate may be too large)`)
  }

  // 7.5rem occupancy vs measured meta+pickup (+ margins)
  const rem = m.rem
  const estimate = 7.5 * rem
  const metaH = metaBox ? metaBox.h : 0
  const pickupH = pickupBox ? pickupBox.h : 0
  const metaPickupGap = (metaBox && pickupBox) ? Math.max(0, pickupBox.top - metaBox.bottom) : 0
  // Occupancy above the two-row grid: meta + its margin + pickup row. Pickup is inside the left column (grid row 1),
  // so the CSS 7.5rem is an estimate of meta + pickup + gaps reserved so the bottom panel does not cover them.
  const usedAbove = (m.meta?.h ?? 0) + (m.meta && m.pickup ? Math.max(0, (m.pickup.top - (m.meta.bottom))) : 0) + (m.pickup?.h ?? 0)
  // Also measure offset from top of page-import to bottom of pickup
  const occupancy = await page.evaluate(() => {
    const pageEl = document.querySelector('.mg-page-import') as HTMLElement | null
    const pickup = document.querySelector('.mg-pickup-row') as HTMLElement | null
    const meta = document.querySelector('.mg-import-meta') as HTMLElement | null
    const aside = document.querySelector('.mg-import-aside') as HTMLElement | null
    const main = document.querySelector('main') as HTMLElement | null
    if (!pageEl || !pickup || !meta || !aside || !main) return null
    const pr = pickup.getBoundingClientRect()
    const mr = meta.getBoundingClientRect()
    const ar = aside.getBoundingClientRect()
    const cr = pageEl.getBoundingClientRect()
    const mainR = main.getBoundingClientRect()
    const pickupOccupied = pr.bottom - cr.top
    const computedMax = getComputedStyle(aside).maxHeight
    return { pickupOccupied, metaH: mr.height, pickupH: pr.height, asideMaxH: ar.height, computedMax, mainH: mainR.height }
  })
  const remCheck = {
    rem,
    estimate75rem: estimate,
    usedAbove,
    metaH,
    pickupH,
    metaPickupGap,
    occupancy,
    comment: 'Implementation uses 7.5rem as a stand-in for meta + compact pickup + gaps. Recorded for design follow-up; a FAIL is only raised when the panel actually covers pickup or the run bar is clipped / too tall for the visible main.',
  }

  // Form scroll inside the panel; run stays at the panel bottom
  if (m.form && m.form.scrollHeight > m.form.clientHeight + 8) {
    const runBefore = runBox
    await page.locator('.mg-import-settings-body').evaluate(el => { el.scrollTop = el.scrollHeight })
    await page.waitForTimeout(200)
    const afterForm = await metrics(page)
    await page.screenshot({ path: recordShot('I5-860-form-scrolled.png'), fullPage: false })
    const runAfter = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
    if (!runAfter) fails.push('开始入库 disappeared after form scroll at 860')
    else if (runBefore && Math.abs(runAfter.top - runBefore.top) > 6) fails.push('开始入库 moved with the form at 860')
    if (afterForm.form && afterForm.form.scrollTop < 20) fails.push('form did not scroll at 860')
    remCheck['formAfter'] = afterForm.form
  } else {
    fails.push('form body did not overflow at 860 after injected min-height')
  }

  await formStyle.evaluate((el: HTMLElement) => el.remove()).catch(() => {})
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, fails, click, remCheck, m, titleBox, runBox, pickupBox, asideBox }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I6 very narrow: only the dropzone may wrap; status stays in meta', async ({ page }) => {
  const id = 'I6'
  const fails: string[] = []
  // 580px with sidebar (200) still visible (sidebar hides only at ≤760? Wait, 580 < 760 so sidebar is hidden).
  // At 580 the sidebar is off-canvas (max-width: 760). Content is full width minus main padding.
  // The spec also allows wrap when two buttons + ~200px dropzone do not fit. Test both 580 and a width
  // just above 760 if needed. Primary case: 580 as specified.
  await page.setViewportSize({ width: 580, height: 900 })
  await gotoImport(page)
  const choose = page.getByRole('button', { name: '选择图片' })
  const dirBtn = page.getByRole('button', { name: '选择目录' })
  const dirFallback = page.getByText('目录选择不可用')
  const second = (await dirBtn.count()) ? dirBtn : dirFallback
  const drop = page.locator('.mg-pickup-row .mg-dropzone')
  const status = page.locator('.mg-import-meta .mg-status')
  const format = page.locator('.mg-import-meta').getByText(/JPG、PNG、WebP/)
  const bChoose = await boxOf(choose)
  const bSecond = await boxOf(second)
  const bDrop = await boxOf(drop)
  const bStatus = await boxOf(status)
  const bFormat = await boxOf(format)
  const bPickup = await boxOf(page.locator('.mg-pickup-row'))
  const m = await metrics(page)
  await page.screenshot({ path: recordShot('I6-580.png'), fullPage: false })

  if (!bChoose || !bSecond || !bDrop || !bStatus || !bPickup) {
    fails.push('missing geometry at 580')
  } else {
    if (!sameRow(bChoose, bSecond)) fails.push('the two chooser controls are no longer side by side at 580')
    const dropWrapped = bDrop.top >= Math.min(bChoose.bottom, bSecond.bottom) - 2 && !sameRow(bChoose, bDrop)
    const dropSame = sameRow(bChoose, bDrop)
    const available = bPickup.w
    const need = (bChoose.w + bSecond.w + 10 + 10 + 200)
    if (!dropWrapped && !dropSame) fails.push('dropzone is neither on the button row nor clearly wrapped below it')
    // Status / format must not return to the button row
    if (sameRow(bStatus, bChoose) && verticallyOverlaps(bStatus, bChoose, 8) && Math.abs(bStatus.top - bChoose.top) < 20) {
      fails.push('status chip returned to the button row')
    }
    if (bFormat && sameRow(bFormat, bChoose) && verticallyOverlaps(bFormat, bChoose, 8) && Math.abs(bFormat.top - bChoose.top) < 20) {
      fails.push('format hint returned to the button row')
    }
    const statusInPickup = fullyIn(bStatus, bPickup, 4)
    if (statusInPickup) fails.push('status chip is inside the pickup row at 580')
    RESULTS.points[id] = { dropWrapped, dropSame, available, need }
  }
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { ...(RESULTS.points[id] || {}), verdict, fails, viewport: m.viewport, boxes: { bChoose, bSecond, bDrop, bStatus, bFormat, bPickup }, metrics: m }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I7 short viewport: form scrolls, run not clipped, no dual window scroll', async ({ page }) => {
  const id = 'I7'
  const fails: string[] = []
  // 100dvh - topbar 56 - main padding 28 = remaining. Want remaining < 560 ⇒ viewport height < 644.
  await page.setViewportSize({ width: 1440, height: 600 })
  await gotoImport(page)
  await fillQueue(page, 20)
  const formStyle = await page.addStyleTag({ content: '.mg-import-settings-body .ia-form { min-height: 1400px !important; }' })
  await page.waitForTimeout(200)
  const m = await metrics(page)
  const remaining = (m.main?.h ?? (600 - 56 - 28))
  const formVis = m.form?.clientHeight ?? m.form?.h ?? 0
  await page.screenshot({ path: recordShot('I7-short-600.png'), fullPage: false })

  if (m.viewport.h >= 644) fails.push(`viewport height ${m.viewport.h} did not go below the 644px CSS narrow-height breakpoint`)
  if (remaining >= 560) {
    // still valid if form vis < 160, otherwise record and continue measurements
    RESULTS.notes.push(`I7 remaining main height ${remaining} was not < 560; form visible ${formVis}`)
  }
  const runBox = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  const titleBox = await boxOf(page.locator('.mg-import-settings-head h2'))
  const mainBox = m.main
  if (!runBox) fails.push('开始入库 has no box at short height')
  else {
    if (mainBox && !fullyIn(runBox, mainBox as any, 4)) fails.push('开始入库 is clipped out of main at short height')
    if (runBox.h < 8) fails.push('开始入库 is vertically collapsed')
  }
  const click = await visibleClickable(page, page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
  if (!click.ok) fails.push(`开始入库 not hit-testable at short height: ${click.reason}`)
  if (m.windowScroll.maxY > 2 || m.windowScroll.y > 1) fails.push(`window is scrollable / scrolled (y=${m.windowScroll.y}, max=${m.windowScroll.maxY})`)
  if (m.html.overflow !== 'hidden' && m.html.scrollHeight > m.html.clientHeight + 2) {
    fails.push(`html overflow ${m.html.overflow} and scrollHeight>clientHeight`)
  }
  if (m.main && m.main.scrollHeight > m.main.clientHeight + 8 && m.main.overflowY !== 'hidden') {
    // Dual scroll: main + inner. Fail only if scrolling main would take the rail out — try it.
    const beforeRun = runBox
    await page.locator('main').evaluate(el => { el.scrollTop = el.scrollHeight })
    await page.waitForTimeout(150)
    const after = await metrics(page)
    const runAfter = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
    await page.screenshot({ path: recordShot('I7-after-main-scroll-attempt.png'), fullPage: false })
    if (after.main && after.main.scrollTop > 2) {
      if (!runAfter || (after.main && !fullyIn(runAfter, after.main as any, 4))) {
        fails.push('scrolling main at short height moved 开始入库 out of the visible main (dual scrollbar)')
      }
    }
    RESULTS.points[id] = { ...(RESULTS.points[id] || {}), afterMainScroll: { scrollTop: after.main?.scrollTop, runAfter, beforeRun } }
  }
  if (m.form && m.form.scrollHeight > m.form.clientHeight + 8) {
    await page.locator('.mg-import-settings-body').evaluate(el => { el.scrollTop = el.scrollHeight })
    await page.waitForTimeout(150)
    const runAfter = await boxOf(page.getByRole('button', { name: /开始入库|继续入库|取消入库/ }))
    if (!runAfter) fails.push('开始入库 disappeared after form scroll at short height')
    else if (runBox && Math.abs(runAfter.top - runBox.top) > 6) fails.push('开始入库 moved with the form at short height')
  }

  // dropzone subtitle should hide under the 644px height media (optional layout, not a hard fail)
  const subVisible = await page.locator('.mg-pickup-row .mg-dropzone p').isVisible().catch(() => false)

  await formStyle.evaluate((el: HTMLElement) => el.remove()).catch(() => {})
  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { ...(RESULTS.points[id] || {}), verdict, fails, remaining, formVis, click, runBox, titleBox, m, subVisible }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I8 business regression (local queue only, no real ingest)', async ({ page }) => {
  const id = 'I8'
  const fails: string[] = []
  const notes: string[] = [
    'Did not POST /api/index or write a business DB. Local temp images only. Start/cancel/retry checked as present entry points; cancel was exercised after frontend indexing-state injection, not a live run.',
  ]
  await page.setViewportSize({ width: 1440, height: 1000 })
  await gotoImport(page)

  // Choose-image entry: hidden file input still wired
  const fileInput = page.locator('input[aria-label="选择待入库图片"]')
  await expect(fileInput).toHaveCount(1)
  const dirBtn = page.getByRole('button', { name: '选择目录' })
  const dirFallback = page.getByText('目录选择不可用')
  const dirAvailable = await dirBtn.count() > 0
  if (dirAvailable) {
    await expect(page.locator('input[aria-label="选择待入库目录"]')).toHaveCount(1)
  } else {
    await expect(dirFallback).toBeVisible()
    notes.push('目录选择不可用 placeholder is shown in this browser.')
  }

  // Drag-drop onto the dropzone
  const drop = page.locator('.mg-pickup-row .mg-dropzone')
  const payload = pngBytes()
  await drop.evaluate(async (el, b64) => {
    const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0))
    const file = new File([bin], 'qa-drag-drop.png', { type: 'image/png' })
    const dt = new DataTransfer()
    dt.items.add(file)
    el.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }))
    el.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }))
  }, payload.toString('base64'))
  await page.waitForTimeout(300)
  const afterDrop = await page.locator('.mg-queue-heading h3 span').innerText()
  if (parseInt(afterDrop, 10) < 1) fails.push('drop did not enqueue a local image')

  // Also add via the file input (选择图片 path)
  await fillQueue(page, 3) // adds 3 more uniquely named files
  const count = parseInt(await page.locator('.mg-queue-heading h3 span').innerText(), 10)
  if (count < 2) fails.push(`queue count after input+drop is ${count}`)

  // 单独标注 dialog
  const single = page.getByRole('button', { name: '单独标注' }).first()
  await expect(single).toBeVisible()
  await single.click()
  const dialog = page.locator('[role="dialog"], .dialog, [data-dialog], .mg-dialog').first()
  // Dialog.tsx is not in this knife; just observe that a dialog or overlay opened.
  const dialogOpen = await page.locator('text=单独标注').count() > 0 || await page.getByRole('dialog').count() > 0
  // Look for common close/cancel
  const overlayVisible = await page.evaluate(() => {
    const els = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
    return els.some(el => (el as HTMLElement).offsetParent !== null || getComputedStyle(el).display !== 'none')
  })
  await page.screenshot({ path: recordShot('I8-single-annotate.png'), fullPage: false })
  if (!overlayVisible && !dialogOpen) {
    notes.push('Could not confirm a dialog role after clicking 单独标注; Dialog.tsx is out of knife scope unless this knife clearly broke it.')
  }
  // Close if possible
  const closer = page.getByRole('button', { name: /取消|关闭|完成/ }).first()
  if (await closer.count()) {
    await closer.click().catch(() => {})
  } else {
    await page.keyboard.press('Escape')
  }
  await page.waitForTimeout(200)

  // Start / cancel / retry entry points
  const start = page.getByRole('button', { name: /开始入库|继续入库/ })
  await expect(start).toBeVisible()
  const startDisabled = await start.isDisabled()
  notes.push(`开始入库 disabled=${startDisabled} (layout-visible; may be disabled by existing annotation blockers without a backend).`)

  // Inject indexing to reveal 取消入库, click it (stopIndexing is local).
  await setIndexingState(page, true)
  await page.waitForTimeout(200)
  const cancel = page.getByRole('button', { name: '取消入库' })
  const cancelVisible = await cancel.isVisible().catch(() => false)
  if (!cancelVisible) fails.push('取消入库 did not appear after indexing-state injection')
  else {
    await cancel.click()
    await page.waitForTimeout(200)
    const back = await page.locator('.mg-import-meta .mg-status').innerText()
    // stopIndexing should set indexing false — if click hooked up, status returns
    notes.push(`after clicking 取消入库, meta text=${JSON.stringify(back)}`)
  }
  await setIndexingState(page, false).catch(() => {})

  // 查看任务中心
  const jump = page.getByRole('button', { name: /查看任务中心/ })
  await expect(jump).toBeVisible()
  await jump.click()
  await page.waitForTimeout(300)
  const onTasks = await page.evaluate(() => {
    const u = new URL(location.href)
    return u.searchParams.get('view') === 'tasks' || !!document.querySelector('.mg-page:not(.mg-page-import)')
  })
  const tasksTitle = await page.locator('.topbar-title h1').innerText().catch(() => '')
  await page.screenshot({ path: recordShot('I8-tasks-center.png'), fullPage: false })
  if (tasksTitle && !/入库任务/.test(tasksTitle) && !onTasks) fails.push(`任务中心 jump landed on title=${tasksTitle}`)

  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, fails, notes, dirAvailable, afterDrop, count, overlayVisible, startDisabled, cancelVisible, tasksTitle, onTasks }
  expect(fails, fails.join(' | ')).toEqual([])
})

test('I9 nav/brand and other pages not harmed by import-page', async ({ page }) => {
  const id = 'I9'
  const fails: string[] = []
  await page.setViewportSize({ width: 1440, height: 1000 })

  async function snapshot(view: 'import' | 'library' | 'tasks' | 'search') {
    const navName: Record<string, string> = { import: '上传与导入', library: '图片数据', tasks: '入库任务', search: '图搜图' }
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.app-shell', { timeout: 30_000 })
    await page.getByRole('button', { name: navName[view] }).click()
    if (view === 'import') await page.waitForSelector('.mg-page-import', { timeout: 15_000 })
    else await page.waitForSelector('main', { timeout: 15_000 })
    await page.waitForTimeout(200)
    const shot = `${id}-${view}.png`
    await page.screenshot({ path: recordShot(shot), fullPage: false })
    return page.evaluate((view) => {
      const main = document.querySelector('main') as HTMLElement | null
      const pageEl = document.querySelector('.mg-page') as HTMLElement | null
      const sidebar = document.querySelector('.sidebar') as HTMLElement | null
      const cs = getComputedStyle(document.documentElement)
      const shell = getComputedStyle(document.querySelector('.app-shell') || document.body)
      const mainCs = main ? getComputedStyle(main) : null
      const pageCs = pageEl ? getComputedStyle(pageEl) : null
      const nav = sidebar ? getComputedStyle(sidebar) : null
      return {
        view,
        title: document.querySelector('.topbar-title h1')?.textContent ?? '',
        mainClass: main?.className ?? '',
        pageClass: pageEl?.className ?? '',
        hasImportPageClass: !!main?.classList.contains('import-page'),
        hasMgPageImport: !!pageEl?.classList.contains('mg-page-import'),
        primary: cs.getPropertyValue('--primary').trim(),
        sidebarWidthVar: cs.getPropertyValue('--sidebar-width').trim(),
        sidebarWidthPx: sidebar ? sidebar.getBoundingClientRect().width : null,
        sidebarBg: nav?.backgroundColor ?? null,
        mainOverflow: mainCs?.overflow + '/' + mainCs?.overflowY,
        pageOverflow: pageCs ? pageCs.overflow + '/' + pageCs.overflowY : null,
        htmlOverflow: getComputedStyle(document.documentElement).overflow,
        navItems: Array.from(document.querySelectorAll('.nav-item')).map(el => (el as HTMLElement).getAttribute('aria-label') || el.textContent?.trim()?.split('\n')[0]),
      }
    }, view)
  }

  const importSnap = await snapshot('import')
  const librarySnap = await snapshot('library')
  const tasksSnap = await snapshot('tasks')
  const searchSnap = await snapshot('search')

  if (!importSnap.hasImportPageClass || !importSnap.hasMgPageImport) fails.push('import view is missing import-page / mg-page-import')
  if (librarySnap.hasImportPageClass || librarySnap.hasMgPageImport) fails.push('library view picked up import-page or mg-page-import')
  if (tasksSnap.hasImportPageClass || tasksSnap.hasMgPageImport) fails.push('tasks view picked up import-page or mg-page-import')
  if (searchSnap.hasImportPageClass || searchSnap.hasMgPageImport) fails.push('search view picked up import-page or mg-page-import')
  if (!/library-page/.test(librarySnap.mainClass) && !/mg-page-library/.test(librarySnap.pageClass)) {
    fails.push(`library classes unexpected: main=${librarySnap.mainClass} page=${librarySnap.pageClass}`)
  }
  if (!/search-page/.test(searchSnap.mainClass)) fails.push(`search main class unexpected: ${searchSnap.mainClass}`)
  // Brand
  const expectedPrimary = '#2c56a0'
  for (const s of [importSnap, librarySnap, tasksSnap, searchSnap]) {
    const p = s.primary.replace(/\s/g, '').toLowerCase()
    if (p !== expectedPrimary) fails.push(`${s.view} --primary is ${s.primary}, expected ${expectedPrimary}`)
    if (s.sidebarWidthVar !== '200px' && Math.abs((s.sidebarWidthPx ?? 0) - 200) > 2) {
      fails.push(`${s.view} sidebar width var=${s.sidebarWidthVar} px=${s.sidebarWidthPx}`)
    }
  }
  const expectedNav = ['图搜图', '图片数据', '上传与导入', '入库任务']
  for (const name of expectedNav) {
    if (!importSnap.navItems.some((n: string) => n?.includes(name))) fails.push(`nav missing ${name}`)
  }

  const verdict: Verdict = fails.length ? 'FAIL' : 'PASS'
  RESULTS.points[id] = { verdict, fails, importSnap, librarySnap, tasksSnap, searchSnap }
  expect(fails, fails.join(' | ')).toEqual([])
})
