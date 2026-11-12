import { test } from '../../frontend/node_modules/@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

test('supplemental I5 occupancy at scrollTop=0 and I6 wrap at 400px', async ({ page }) => {
  const out: any = {}
  async function gotoImport() {
    const desired = page.viewportSize()
    const narrow = Boolean(desired && desired.width <= 760)
    if (narrow) await page.setViewportSize({ width: 1440, height: desired!.height })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.app-shell', { timeout: 30_000 })
    await page.getByRole('button', { name: '上传与导入' }).click()
    await page.waitForSelector('.mg-page-import', { timeout: 20_000 })
    if (narrow && desired) await page.setViewportSize(desired)
    await page.waitForTimeout(200)
  }
  async function fill(n: number) {
    const files = Array.from({ length: n }, (_, i) => ({
      name: `qa-rem-${i}.png`, mimeType: 'image/png', buffer: png,
    }))
    await page.locator('input[aria-label="选择待入库图片"]').setInputFiles(files)
    await page.waitForFunction((n) => {
      const el = document.querySelector('.mg-queue-heading h3 span')
      return el && parseInt(el.textContent || '0', 10) >= n
    }, n)
  }

  await page.setViewportSize({ width: 860, height: 1000 })
  await gotoImport()
  await fill(40)
  const atTop = await page.evaluate(() => {
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize)
    const pageEl = document.querySelector('.mg-page-import') as HTMLElement
    const meta = document.querySelector('.mg-import-meta') as HTMLElement
    const pickup = document.querySelector('.mg-pickup-row') as HTMLElement
    const aside = document.querySelector('.mg-import-aside') as HTMLElement
    const main = document.querySelector('main') as HTMLElement
    const pr = pickup.getBoundingClientRect()
    const mr = meta.getBoundingClientRect()
    const ar = aside.getBoundingClientRect()
    const cr = pageEl.getBoundingClientRect()
    const mainR = main.getBoundingClientRect()
    const overlap = Math.min(pr.bottom, ar.bottom) - Math.max(pr.top, ar.top)
    return {
      rem, estimate75: 7.5 * rem,
      metaH: mr.height, pickupH: pr.height, gap: pr.top - mr.bottom,
      usedAbove: (mr.bottom - cr.top) + 0, // from page top through meta; plus pickup?
      fromPageTopToPickupBottom: pr.bottom - cr.top,
      fromMainTopToPickupBottom: pr.bottom - mainR.top,
      pickup: { top: pr.top, bottom: pr.bottom, h: pr.height },
      aside: { top: ar.top, bottom: ar.bottom, h: ar.height, maxHeight: getComputedStyle(aside).maxHeight, position: getComputedStyle(aside).position },
      main: { top: mainR.top, bottom: mainR.bottom, h: mainR.height },
      overlapPx: overlap,
      coversPickup: overlap > 1,
      leftoverBelowPickup: ar.top - pr.bottom,
    }
  })
  await page.screenshot({ path: path.join(__dirname, 'screenshots', 'I5-860-scrolltop0.png'), fullPage: false })
  out.i5_at_top = atTop

  await page.setViewportSize({ width: 400, height: 800 })
  await page.waitForTimeout(250)
  const at400 = await page.evaluate(() => {
    const choose = document.querySelector('.mg-pickup-row > .mg-btn.mg-primary') as HTMLElement
    const second = document.querySelector('.mg-pickup-row > .mg-btn.mg-secondary, .mg-pickup-row > .mg-toolbar-summary') as HTMLElement
    const drop = document.querySelector('.mg-pickup-row .mg-dropzone') as HTMLElement
    const status = document.querySelector('.mg-import-meta .mg-status') as HTMLElement
    const pickup = document.querySelector('.mg-pickup-row') as HTMLElement
    const b = (el: HTMLElement) => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, w: r.width, h: r.height } }
    const bc = b(choose), bs = b(second), bd = b(drop), bst = b(status), bp = b(pickup)
    const sameRow = (a: any, c: any) => Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top) >= 4 && !(c.top >= a.bottom - 2 && c.top > a.top + 24)
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      buttonsSameRow: sameRow(bc, bs),
      dropSameRowAsButtons: sameRow(bc, bd),
      dropWrapped: bd.top >= Math.min(bc.bottom, bs.bottom) - 2 && !sameRow(bc, bd),
      statusInPickup: bst.top >= bp.top - 2 && bst.bottom <= bp.bottom + 2 && bst.left >= bp.left - 2,
      statusInMeta: !!status.closest('.mg-import-meta'),
      boxes: { bc, bs, bd, bst, bp },
    }
  })
  await page.screenshot({ path: path.join(__dirname, 'screenshots', 'I6-400.png'), fullPage: false })
  out.i6_at_400 = at400
  fs.writeFileSync(path.join(__dirname, 'supplemental-i5-i6.json'), JSON.stringify(out, null, 2))
})
