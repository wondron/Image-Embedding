import { test, expect, type Page, type Locator } from '../frontend/node_modules/@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const QA = path.resolve(__dirname)
const SHOTS = path.join(QA, 'scene-detail-layout-screenshots')
const API = 'http://127.0.0.1:57401'
const FE = 'http://127.0.0.1:57402'

type Measure = {
  inner: number
  dataSceneWidth: string | undefined
  containerType: string
  viewportWidth: number
  tabs: string[]
  hasBadges: boolean
  headerScroll: { scrollWidth: number; clientWidth: number; overflows: boolean }
  detailsScrollX: { scrollWidth: number; clientWidth: number; overflows: boolean }
  titleOverflows: boolean
}

const evidence: Record<string, unknown> = {
  startedAt: new Date().toISOString(),
  screenshots: [] as string[],
  measures: [] as unknown[],
}

function shotPath(name: string) {
  const file = path.join(SHOTS, `${name}.png`)
  ;(evidence.screenshots as string[]).push(file)
  return file
}

async function measure(page: Page, label: string): Promise<Measure> {
  const result = await page.evaluate(() => {
    const el = document.querySelector('.scene-details') as HTMLElement | null
    if (!el) return null
    const s = getComputedStyle(el)
    const inner = el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight)
    const header = document.querySelector('.scene-detail-header') as HTMLElement | null
    const name = document.querySelector('.scene-detail-name') as HTMLElement | null
    const headerScroll = header
      ? { scrollWidth: header.scrollWidth, clientWidth: header.clientWidth, overflows: header.scrollWidth > header.clientWidth + 1 }
      : { scrollWidth: 0, clientWidth: 0, overflows: false }
    const detailsScrollX = { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflows: el.scrollWidth > el.clientWidth + 1 }
    const titleOverflows = name ? name.scrollWidth > name.clientWidth + 1 : false
    return {
      inner,
      dataSceneWidth: el.dataset.sceneWidth,
      containerType: s.containerType,
      viewportWidth: window.innerWidth,
      tabs: [...document.querySelectorAll('.scene-detail-tabs [role=tab]')].map(x => (x.textContent || '').trim()),
      hasBadges: !!document.querySelector('.scene-detail-badges'),
      headerScroll,
      detailsScrollX,
      titleOverflows,
    }
  })
  if (!result) throw new Error(`measure failed: .scene-details missing (${label})`)
  const row = { label, ...result }
  ;(evidence.measures as unknown[]).push(row)
  return result
}

async function setRightColumnInner(page: Page, inner: number) {
  await page.evaluate((target) => {
    const details = document.querySelector('.scene-details') as HTMLElement
    const layout = document.querySelector('.scene-layout') as HTMLElement
    const s = getComputedStyle(details)
    const pad = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight)
    layout.style.gridTemplateColumns = `minmax(200px, 1fr) ${Math.round(target + pad)}px`
  }, inner)
  const expected = inner < 480 ? 'compact' : inner < 720 ? 'medium' : 'wide'
  await page.waitForFunction(({ target, expected }) => {
    const el = document.querySelector('.scene-details') as HTMLElement | null
    if (!el) return false
    const s = getComputedStyle(el)
    const current = el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight)
    return Math.abs(current - target) <= 2 && el.dataset.sceneWidth === expected
  }, { target: inner, expected }, { timeout: 5000 })
}

async function resetLayout(page: Page) {
  await page.evaluate(() => {
    const layout = document.querySelector('.scene-layout') as HTMLElement | null
    if (layout) layout.style.gridTemplateColumns = ''
  })
}

async function openScenes(page: Page, query = '/?view=scenes') {
  await page.addInitScript(() => {
    try { localStorage.removeItem('food-atlas-api') } catch { /* ignore */ }
  })
  await page.goto(query, { waitUntil: 'domcontentloaded' })
  const apiBase = await page.evaluate(() => localStorage.getItem('food-atlas-api'))
  if (apiBase !== null && apiBase !== '') {
    await page.evaluate(() => localStorage.removeItem('food-atlas-api'))
    await page.reload({ waitUntil: 'domcontentloaded' })
  }
  await expect(page.locator('.scene-management')).toBeVisible({ timeout: 15_000 })
  const stored = await page.evaluate(() => localStorage.getItem('food-atlas-api'))
  expect(stored === null || stored === '', `API base must be blank for Vite proxy, got ${stored}`).toBeTruthy()
}

function dirItem(page: Page, code: string) {
  return page.locator('.scene-directory-item', { has: page.locator('code', { hasText: code }) })
}

async function selectScene(page: Page, code: string) {
  await dirItem(page, code).click()
  await expect(page.locator('.scene-detail-code')).toHaveText(code)
}

async function statusTexts(page: Page) {
  return page.evaluate(() => ({
    directory: [...document.querySelectorAll('.scene-directory-status p')].map(p => (p.textContent || '').trim()),
    scoped: [...document.querySelectorAll('.scene-scoped-status p')].map(p => (p.textContent || '').trim()),
  }))
}

function rectsOverlap(a: { top: number; bottom: number; left: number; right: number }, b: typeof a, slack = 1) {
  return a.left < b.right - slack && b.left < a.right - slack && a.top < b.bottom - slack && b.top < a.right && a.top < b.bottom - slack && b.top < a.bottom - slack
}

test.describe.configure({ mode: 'serial' })

test('scene-detail-layout L1-L19 isolated GUI', async ({ page, request }) => {
  fs.mkdirSync(SHOTS, { recursive: true })
  const flush = () => {
    evidence.finishedAt = new Date().toISOString()
    fs.writeFileSync(path.join(QA, 'scene-detail-layout-gui-evidence.json'), JSON.stringify(evidence, null, 2), 'utf8')
  }
  try {
  const ping = await request.get(`${API}/api/ping`)
  const fe = await request.get(`${FE}/?view=scenes`)
  const proxied = await request.get(`${FE}/api/scenes`)
  evidence.preflight = { apiPing: ping.status(), frontend: fe.status(), proxiedScenes: proxied.status(), proxiedHasLong: (await proxied.text()).includes('qalongestlegalcode32charactersxx') }
  expect(ping.ok(), `fixture ping ${ping.status()}`).toBeTruthy()
  expect(fe.ok(), `frontend ${fe.status()}`).toBeTruthy()
  expect(proxied.ok(), `proxy /api/scenes ${proxied.status()}`).toBeTruthy()

  await openScenes(page)
  await expect(page.getByRole('button', { name: '全量场景同步' }).first()).toBeVisible()
  await page.screenshot({ path: shotPath('00-unselected-wide'), fullPage: false })

  // ---- Unselected empty state and header sync entry (L14 start, unselected) ----
  const unselectedCopy = page.getByText('请选择左侧场景')
  await expect(unselectedCopy).toBeVisible()
  const unselectedHeaderActions = await page.locator('.scene-detail-actions').count()
  const unselectedBadges = await page.locator('.scene-detail-badges').count()
  evidence.unselectedLanding = { hasSelectPrompt: await unselectedCopy.isVisible(), headerActions: unselectedHeaderActions, badges: unselectedBadges }
  await page.getByRole('button', { name: '全量场景同步' }).first().click()
  await expect(page.locator('.scene-detail-name')).toHaveText('全量场景同步')
  await expect(page.locator('.scene-sync-panel h3')).toHaveText('全量场景同步')
  await expect(page.locator('.scene-detail-badges')).toHaveCount(0)
  await expect(page.locator('.scene-detail-actions')).toHaveCount(0)
  await page.screenshot({ path: shotPath('14-unselected-sync'), fullPage: false })
  const unselectedSync = await measure(page, 'unselected-sync-default-width')
  evidence.unselectedSync = { measure: unselectedSync, title: await page.locator('.scene-detail-name').innerText(), badges: await page.locator('.scene-detail-badges').count(), actions: await page.locator('.scene-detail-actions').count() }

  // ---- Select long fixture scene ----
  await selectScene(page, 'qalongestlegalcode32charactersxx')
  await expect(page.getByRole('tab', { name: '概览' })).toBeVisible()
  await page.getByRole('tab', { name: '概览' }).click()
  const loaded = await measure(page, 'scene4-default-width')
  evidence.loadedLayout = loaded
  expect(loaded.tabs).toContain('全量场景同步')
  expect(loaded.hasBadges).toBeTruthy()
  expect(loaded.containerType === 'inline-size' || loaded.containerType === 'size').toBeTruthy()
  await page.screenshot({ path: shotPath('05-long-scene-default'), fullPage: false })
  await page.locator('.scene-details').screenshot({ path: shotPath('05-long-scene-default-panel') })

  // ---- L1/L4 wide: window stays 1440, right column inner ~720+ ----
  await setRightColumnInner(page, 800)
  const wide = await measure(page, 'L1-inner-800')
  expect(wide.viewportWidth).toBeGreaterThanOrEqual(1280)
  expect(wide.inner).toBeGreaterThanOrEqual(720)
  expect(wide.dataSceneWidth).toBe('wide')
  const actionOrder = await page.evaluate(() => [...document.querySelectorAll('.scene-detail-header .mg-btn, .scene-detail-header button.mg-btn')].map(b => (b.textContent || '').trim()))
  const headerOverflow = wide.headerScroll.overflows || wide.titleOverflows
  await page.screenshot({ path: shotPath('01-wide-inner800'), fullPage: false })
  await page.locator('.scene-details').screenshot({ path: shotPath('01-wide-inner800-panel') })
  evidence.L1 = { measure: wide, actionOrder, headerOverflow }

  // ---- L4: keep viewport, change only right column through the breakpoints ----
  const l4: Record<string, Measure> = {}
  for (const inner of [720, 719, 480, 479]) {
    await setRightColumnInner(page, inner)
    const m = await measure(page, `L4-inner-${inner}`)
    expect(m.viewportWidth).toBeGreaterThanOrEqual(1280)
    expect(Math.abs(m.inner - inner)).toBeLessThanOrEqual(2)
    l4[`inner${inner}`] = m
    await page.locator('.scene-details').screenshot({ path: shotPath(`04-inner${inner}`) })
  }
  evidence.L4 = { viewportRemainedWide: Object.values(l4).every(m => m.viewportWidth >= 1280), points: l4 }
  expect(l4.inner720.dataSceneWidth).toBe('wide')
  expect(l4.inner719.dataSceneWidth).toBe('medium')
  expect(l4.inner480.dataSceneWidth).toBe('medium')
  expect(l4.inner479.dataSceneWidth).toBe('compact')

  // ---- L2 medium 600 ----
  await setRightColumnInner(page, 600)
  const mid = await measure(page, 'L2-inner-600')
  expect(mid.viewportWidth).toBeGreaterThanOrEqual(1280)
  expect(mid.inner).toBeGreaterThanOrEqual(480)
  expect(mid.inner).toBeLessThan(720)
  expect(mid.dataSceneWidth).toBe('medium')
  const midClip = await page.evaluate(() => {
    const header = document.querySelector('.scene-detail-header') as HTMLElement
    const details = document.querySelector('.scene-details') as HTMLElement
    const hr = header.getBoundingClientRect(); const dr = details.getBoundingClientRect()
    return {
      headerWiderThanPanel: hr.width > dr.width + 2,
      overflows: header.scrollWidth > header.clientWidth + 1,
      wrap: getComputedStyle(header).flexWrap,
    }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('02-medium-inner600-panel') })
  evidence.L2 = { measure: mid, midClip }

  // ---- L3 compact 479: stack + delete row + tabs + table ----
  await setRightColumnInner(page, 479)
  const compact = await measure(page, 'L3-inner-479')
  expect(compact.viewportWidth).toBeGreaterThanOrEqual(1280)
  expect(compact.inner).toBeLessThan(480)
  expect(compact.dataSceneWidth).toBe('compact')
  const stack = await page.evaluate(() => {
    const primary = document.querySelector('.scene-detail-primary-actions') as HTMLElement | null
    const danger = document.querySelector('.scene-detail-danger-actions') as HTMLElement | null
    const tabs = document.querySelector('.scene-detail-tabs') as HTMLElement | null
    const buttons = [...(primary?.querySelectorAll('button') || [])].map(b => ({
      text: (b.textContent || '').trim(),
      top: b.getBoundingClientRect().top,
      bottom: b.getBoundingClientRect().bottom,
    }))
    const del = danger?.getBoundingClientRect()
    const lastPrimary = buttons[buttons.length - 1]
    const tabBox = tabs?.getBoundingClientRect()
    const tabOverflow = tabs ? tabs.scrollWidth > tabs.clientWidth + 1 : false
    const fullSyncTab = [...(tabs?.querySelectorAll('[role=tab]') || [])].find(t => (t.textContent || '').includes('全量场景同步')) as HTMLElement | undefined
    return {
      buttons,
      deleteSeparateRow: !!(del && lastPrimary && del.top >= lastPrimary.bottom - 2),
      tabRows: tabs ? (tabBox!.height > 40 ? 'maybe-wrap' : 'single') : 'missing',
      tabHeight: tabBox?.height,
      tabOverflow,
      fullSyncVisible: !!(fullSyncTab && fullSyncTab.getClientRects().length && (fullSyncTab.textContent || '').includes('全量场景同步')),
      fullSyncText: fullSyncTab?.textContent || '',
      titleOverflows: compactNeed(),
    }
    function compactNeed() {
      const name = document.querySelector('.scene-detail-name') as HTMLElement | null
      return !!(name && name.scrollWidth > name.clientWidth + 1)
    }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('03-compact-overview-479') })
  // Table at compact width on oven labels
  await selectScene(page, 'oven')
  await setRightColumnInner(page, 479)
  await page.getByRole('tab', { name: '标签' }).click()
  const table = await page.evaluate(() => {
    const scroller = document.querySelector('.scene-table-scroll') as HTMLElement | null
    const table = document.querySelector('.scene-table') as HTMLElement | null
    if (!scroller || !table) return { present: false }
    return {
      present: true,
      overflowX: getComputedStyle(scroller).overflowX,
      scrollWidth: scroller.scrollWidth,
      clientWidth: scroller.clientWidth,
      canScroll: scroller.scrollWidth > scroller.clientWidth + 1,
    }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('03-compact-labels-table-479') })
  evidence.L3 = { measure: compact, stack, table }

  // ---- L5/L6/L7 long name, description, memberCount (wide enough to read, still record wrap) ----
  await selectScene(page, 'qalongestlegalcode32charactersxx')
  await page.getByRole('tab', { name: '概览' }).click()
  await setRightColumnInner(page, 600)
  const longText = await page.evaluate(() => {
    const name = document.querySelector('.scene-detail-name') as HTMLElement
    const code = document.querySelector('.scene-detail-code') as HTMLElement
    const desc = document.querySelector('.scene-description') as HTMLElement
    const count = document.querySelector('.scene-member-count') as HTMLElement
    const caption = document.querySelector('.scene-member-count')?.parentElement?.querySelector('.scene-detail-caption') as HTMLElement | null
    const details = document.querySelector('.scene-details') as HTMLElement
    const box = details.getBoundingClientRect()
    const outs = (el: HTMLElement) => {
      const r = el.getBoundingClientRect()
      return { text: (el.textContent || '').trim(), overflowWrap: getComputedStyle(el).overflowWrap, overflowsEl: el.scrollWidth > el.clientWidth + 1, rightPastPanel: r.right > box.right + 2, height: r.height }
    }
    return {
      name: { ...outs(name), length: (name.textContent || '').trim().length },
      code: { ...outs(code), length: (code.textContent || '').trim().length, value: (code.textContent || '').trim() },
      desc: { ...outs(desc), length: (desc.textContent || '').trim().length },
      member: { ...outs(count), caption: caption?.textContent || '' },
    }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('05-06-07-long-overview-600') })
  evidence.L5 = longText.name
  evidence.L6 = longText.desc
  evidence.L7 = longText.member

  // ---- L8 badges: general (default+include+active) and qa_badges (inactive+include) ----
  const badgeCheck = async (code: string) => {
    await selectScene(page, code)
    await setRightColumnInner(page, 600)
    return page.evaluate(() => {
      const badges = [...document.querySelectorAll('.scene-detail-badge')].map(el => {
        const r = (el as HTMLElement).getBoundingClientRect()
        return { text: (el.textContent || '').trim(), top: r.top, bottom: r.bottom, left: r.left, right: r.right }
      })
      const texts = badges.map(b => b.text)
      const overlap = badges.some((a, i) => badges.some((b, j) => i < j && a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1))
      return { texts, overlap, wrap: new Set(badges.map(b => Math.round(b.top))).size > 1 || badges.length <= 3 }
    })
  }
  const generalBadges = await badgeCheck('general')
  await page.locator('.scene-details').screenshot({ path: shotPath('08-badges-general') })
  const offBadges = await badgeCheck('qa_badges')
  await page.locator('.scene-details').screenshot({ path: shotPath('08-badges-inactive-include') })
  evidence.L8 = { generalBadges, offBadges }

  // ---- L9 empty labels/rules ----
  await selectScene(page, 'qa_empty')
  await resetLayout(page)
  await page.getByRole('tab', { name: '标签' }).click()
  const emptyLabels = await page.evaluate(() => ({
    emptyTitle: document.querySelector('.scene-tab-empty')?.textContent || '',
    tables: document.querySelectorAll('.scene-tab-body .scene-table').length,
    status: [...document.querySelectorAll('.scene-status-stack p')].map(p => (p.textContent || '').trim()),
    overviewVisible: !!document.querySelector('.scene-overview'),
  }))
  await page.locator('.scene-details').screenshot({ path: shotPath('09-empty-labels') })
  await page.getByRole('tab', { name: '规则' }).click()
  const emptyRules = await page.evaluate(() => ({
    emptyTitle: document.querySelector('.scene-tab-empty')?.textContent || '',
    tables: document.querySelectorAll('.scene-tab-body .scene-table').length,
    status: [...document.querySelectorAll('.scene-status-stack p')].map(p => (p.textContent || '').trim()),
  }))
  await page.locator('.scene-details').screenshot({ path: shotPath('09-empty-rules') })
  await page.getByRole('tab', { name: '概览' }).click()
  const overviewEmptyLeak = await page.evaluate(() => ({
    text: document.querySelector('.scene-overview')?.textContent || '',
    hasEmptyLabelCopy: (document.querySelector('.scene-overview')?.textContent || '').includes('暂无标签'),
    hasEmptyRuleCopy: (document.querySelector('.scene-overview')?.textContent || '').includes('暂无规则'),
  }))
  evidence.L9 = { emptyLabels, emptyRules, overviewEmptyLeak }

  // ---- L18 overview groups ----
  const overview = await page.evaluate(() => {
    const groups = [...document.querySelectorAll('.scene-overview-group')].map(g => ({
      title: g.querySelector('h4')?.textContent || '',
      hasNestedCard: !!g.querySelector('.mg-panel, .card, .scene-overview-group'),
    }))
    const toggles = [...document.querySelectorAll('.scene-overview input[type=checkbox], .scene-overview button[type=submit]')]
    return { groups, directSubmitControls: toggles.length, groupCount: groups.length }
  })
  await page.locator('.scene-overview').screenshot({ path: shotPath('18-overview-groups') })
  evidence.L18 = overview

  // ---- L13 default protection ----
  await selectScene(page, 'general')
  await page.getByRole('tab', { name: '概览' }).click()
  const protection = await page.evaluate(() => {
    const disable = [...document.querySelectorAll('.scene-detail-actions button')].map(b => ({ text: (b.textContent || '').trim(), disabled: (b as HTMLButtonElement).disabled }))
    const notes = [...document.querySelectorAll('#scene-protection-note, .scene-scoped-status p')].map(p => (p.textContent || '').trim())
    return { disable, notes, noteCount: document.querySelectorAll('#scene-protection-note').length }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('13-default-protection') })
  const edit = page.getByRole('button', { name: '编辑' }).first()
  await edit.click()
  const dialog = page.locator('dialog.dialog')
  await expect(dialog).toBeVisible()
  const includeAllDisabled = await dialog.locator('label', { hasText: '全包含' }).locator('input').isDisabled()
  const activeDisabled = await dialog.locator('label', { hasText: '启用' }).locator('input').isDisabled()
  const dialogText = await dialog.innerText()
  await dialog.screenshot({ path: shotPath('13-default-edit-dialog') })
  await dialog.getByRole('button', { name: '取消' }).click()
  await expect(dialog).toHaveCount(0)
  evidence.L13 = { protection, includeAllDisabled, activeDisabled, dialogText }

  // ---- L16/L17 focus return, keyboard, sticky ----
  await selectScene(page, 'qalongestlegalcode32charactersxx')
  await setRightColumnInner(page, 600)
  await page.getByRole('tab', { name: '概览' }).click()
  const trigger = page.getByRole('button', { name: '编辑' }).first()
  await trigger.focus()
  await trigger.press('Enter')
  await expect(page.locator('dialog.dialog')).toBeVisible()
  await expect(page.locator('dialog.dialog [data-dialog-initial-focus]')).toBeFocused()
  await page.locator('dialog.dialog').screenshot({ path: shotPath('16-dialog-open-focus') })
  await page.locator('dialog.dialog').getByRole('button', { name: '关闭弹窗' }).click()
  await expect(page.locator('dialog.dialog')).toHaveCount(0)
  const focusAfterClose = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.textContent?.trim() || document.activeElement?.tagName)
  evidence.L16_focus = { focusAfterClose }
  // Keyboard tabs
  const tablist = page.locator('[role=tablist].scene-detail-tabs')
  await page.getByRole('tab', { name: '概览' }).focus()
  await page.keyboard.press('ArrowRight')
  const afterRight = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.textContent?.trim())
  await page.keyboard.press('End')
  const afterEnd = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.textContent?.trim())
  await page.keyboard.press('Home')
  const afterHome = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.textContent?.trim())
  await page.keyboard.press('Enter')
  evidence.L17 = { afterRight, afterEnd, afterHome, selectedAfterHome: await page.locator('[role=tab][aria-selected=true]').innerText() }
  // Sticky vs focus: make overview tall, scroll, focus caption/edit, check occlusion
  await page.getByRole('tab', { name: '概览' }).click()
  const sticky = await page.evaluate(() => {
    const scroll = document.querySelector('.scene-detail-scroll') as HTMLElement
    const tabs = document.querySelector('.scene-detail-tabs') as HTMLElement
    const header = document.querySelector('.scene-detail-header') as HTMLElement
    scroll.scrollTop = 80
    const edit = document.querySelector('.scene-group-heading .text-button') as HTMLElement | null
    if (edit) edit.focus()
    const er = edit?.getBoundingClientRect()
    const hr = header.getBoundingClientRect()
    const tr = tabs.getBoundingClientRect()
    const coveredByHeader = !!(er && er.top < hr.bottom - 1 && er.bottom > hr.top + 1)
    const coveredByTabs = !!(er && er.top < tr.bottom - 1 && er.bottom > tr.top + 1)
    return {
      stickyHeading: (document.querySelector('.scene-details') as HTMLElement).dataset.stickyHeading,
      headerPos: getComputedStyle(header).position,
      tabsPos: getComputedStyle(tabs).position,
      coveredByHeader,
      coveredByTabs,
      editTop: er?.top,
      headerBottom: hr.bottom,
      tabsBottom: tr.bottom,
      scrollPaddingTop: getComputedStyle(scroll).scrollPaddingTop,
    }
  })
  await page.locator('.scene-details').screenshot({ path: shotPath('16-sticky-scroll') })
  evidence.L16_sticky = sticky

  // ---- L10 loading ----
  await request.get(`${API}/qa/delay-next-catalog?ms=2500`)
  const refresh = page.getByRole('button', { name: '刷新' })
  const loadStart = Date.now()
  await refresh.click()
  const loading = page.locator('.scene-loading')
  await expect(loading).toBeVisible({ timeout: 2000 })
  const loadingState = await page.evaluate(() => ({
    text: document.querySelector('.scene-loading')?.textContent || '',
    fakeTableDuringLoad: document.querySelectorAll('.scene-table tbody tr').length,
    directoryStillPresent: document.querySelectorAll('.scene-directory-item').length,
  }))
  await page.screenshot({ path: shotPath('10-loading') })
  await expect(loading).toHaveCount(0, { timeout: 8000 })
  evidence.L10 = { ...loadingState, waitedMs: Date.now() - loadStart }

  // ---- L11 catalog failure ----
  const beforeItems = await page.locator('.scene-directory-item').count()
  await request.get(`${API}/qa/fail-next-catalog`)
  await refresh.click()
  const catalogError = page.locator('.scene-directory-status .mg-form-error')
  await expect(catalogError).toBeVisible()
  const afterFailItems = await page.locator('.scene-directory-item').count()
  await page.screenshot({ path: shotPath('11-catalog-error') })
  evidence.L11_catalog = { beforeItems, afterFailItems, error: await catalogError.innerText() }
  await catalogError.getByRole('button', { name: '重试刷新' }).click()
  await expect(catalogError).toHaveCount(0, { timeout: 8000 })

  // ---- L12 two-state notices + pendingSync ----
  await selectScene(page, 'qa_empty')
  await page.getByRole('tab', { name: '概览' }).click()
  await page.getByRole('button', { name: '编辑' }).first().click()
  const editor = page.locator('dialog.dialog')
  await expect(editor).toBeVisible()
  const desc = editor.locator('textarea')
  await desc.fill('第一次保存应返回需要同步')
  await editor.getByRole('button', { name: '保存' }).click()
  await expect(editor).toHaveCount(0, { timeout: 10_000 })
  const afterTrue = await statusTexts(page)
  await page.screenshot({ path: shotPath('12-save-true-pending') })
  evidence.L12_true = afterTrue

  await page.getByRole('button', { name: '编辑' }).first().click()
  await expect(page.locator('dialog.dialog')).toBeVisible()
  const heat = page.locator('dialog.dialog').locator('label', { hasText: '输出加热信息' }).locator('input')
  const wasChecked = await heat.isChecked()
  if (wasChecked) await heat.uncheck(); else await heat.check()
  await page.locator('dialog.dialog').getByRole('button', { name: '保存' }).click()
  await expect(page.locator('dialog.dialog')).toHaveCount(0, { timeout: 10_000 })
  const afterFalse = await statusTexts(page)
  await page.screenshot({ path: shotPath('12-save-false-pending-remains') })
  evidence.L12_false = afterFalse

  await selectScene(page, 'oven')
  const afterSwitch = await statusTexts(page)
  await page.screenshot({ path: shotPath('12-switch-scene-notice-scope') })
  await page.goto('/?view=scenes')
  await expect(page.getByText('请选择左侧场景')).toBeVisible()
  const afterUnselect = await statusTexts(page)
  await page.screenshot({ path: shotPath('12-unselected-pending-visible') })
  evidence.L12_scope = { afterSwitch, afterUnselect }

  // ---- L11 write failure (name QAFAIL) ----
  await selectScene(page, 'qa_empty')
  const descBeforeFail = await page.locator('.scene-description').innerText()
  await page.getByRole('button', { name: '编辑' }).first().click()
  await expect(page.locator('dialog.dialog')).toBeVisible()
  await page.locator('dialog.dialog').locator('input').nth(1).fill('名称QAFAIL触发失败')
  await page.locator('dialog.dialog').getByRole('button', { name: '保存' }).click()
  const writeError = page.locator('dialog.dialog .mg-form-error, .scene-scoped-status .mg-form-error, .scene-directory-status .mg-form-error')
  await expect(writeError.first()).toBeVisible({ timeout: 10_000 })
  const writeFail = {
    error: await writeError.first().innerText(),
    dialogOpen: await page.locator('dialog.dialog').count(),
    descriptionUnchanged: await page.locator('.scene-description').count() ? await page.locator('.scene-description').innerText() : descBeforeFail,
    directoryCount: await page.locator('.scene-directory-item').count(),
  }
  await page.screenshot({ path: shotPath('11-write-error') })
  if (await page.locator('dialog.dialog').count()) {
    await page.locator('dialog.dialog').getByRole('button', { name: '取消' }).click()
  }
  evidence.L11_write = writeFail

  // ---- L19 SCENE_IN_USE and rule update/create failure ----
  await selectScene(page, 'oven')
  await page.getByRole('tab', { name: '标签' }).click()
  const inUseRow = page.locator('.scene-table tbody tr', { hasText: 'qa_in_use' })
  await expect(inUseRow).toBeVisible()
  await inUseRow.getByRole('button', { name: '删除' }).click()
  const confirm = page.locator('dialog.dialog', { hasText: '删除标签' })
  await expect(confirm).toBeVisible()
  await confirm.getByRole('button', { name: '确认' }).click()
  const inUseError = page.locator('dialog.dialog .mg-form-error, .scene-scoped-status .mg-form-error')
  await expect(inUseError.first()).toBeVisible({ timeout: 10_000 })
  const inUseStill = await inUseRow.count()
  const inUseEvidence = {
    error: await inUseError.first().innerText(),
    rowRemains: inUseStill > 0,
    errorLocations: await page.locator('dialog.dialog .mg-form-error').count() + await page.locator('.scene-scoped-status .mg-form-error').count(),
  }
  await page.screenshot({ path: shotPath('19-label-in-use') })
  if (await confirm.count()) await confirm.getByRole('button', { name: '取消' }).click()

  await page.getByRole('tab', { name: '规则' }).click()
  const rulesBefore = await page.locator('.scene-table tbody tr').count()
  await page.getByRole('button', { name: '新建规则' }).click()
  const ruleDlg = page.locator('dialog.dialog', { hasText: '新建规则' })
  await expect(ruleDlg).toBeVisible()
  await ruleDlg.locator('label', { hasText: '按菜系' }).click()
  await ruleDlg.locator('input[placeholder="如 chinese"]').fill('qa_force_fail')
  await ruleDlg.getByRole('button', { name: '保存' }).click()
  await expect(ruleDlg.locator('.mg-form-error')).toBeVisible({ timeout: 10_000 })
  const cuisineValue = await ruleDlg.locator('input[placeholder="如 chinese"]').inputValue()
  const rulesAfterFail = await page.locator('.scene-table tbody tr').count()
  const createFail = { error: await ruleDlg.locator('.mg-form-error').innerText(), cuisineValue, rulesBefore, rulesAfterFail, dialogOpen: await ruleDlg.count() }
  await ruleDlg.screenshot({ path: shotPath('19-createRule-error') })
  await ruleDlg.getByRole('button', { name: '取消' }).click()

  const existing = page.locator('.scene-table tbody tr').first()
  await existing.getByRole('button', { name: '编辑' }).click()
  const editDlg = page.locator('dialog.dialog', { hasText: '编辑规则' })
  await expect(editDlg).toBeVisible()
  await editDlg.locator('label', { hasText: '按菜系' }).click()
  await editDlg.locator('input[placeholder="如 chinese"]').fill('qa_force_fail')
  await editDlg.getByRole('button', { name: '保存' }).click()
  await expect(editDlg.locator('.mg-form-error')).toBeVisible({ timeout: 10_000 })
  const updateFail = { error: await editDlg.locator('.mg-form-error').innerText(), cuisineValue: await editDlg.locator('input[placeholder="如 chinese"]').inputValue(), rules: await page.locator('.scene-table tbody tr').count(), dialogOpen: await editDlg.count() }
  await editDlg.screenshot({ path: shotPath('19-updateRule-error') })
  await editDlg.getByRole('button', { name: '取消' }).click()
  evidence.L19 = { inUseEvidence, createFail, updateFail }

  // ---- L14/L15 sync layout and client behavior with fixture ----
  await page.goto('/?view=scenes&sceneTab=sync')
  await expect(page.locator('.scene-sync-panel h3')).toHaveText('全量场景同步')
  const tabLabel = await page.getByRole('tab', { name: '全量场景同步' }).innerText()
  const headerBtn = await page.getByRole('button', { name: '全量场景同步' }).first().isVisible()
  await page.locator('.scene-sync-panel').screenshot({ path: shotPath('14-15-sync-idle') })
  await page.locator('.scene-sync-panel input[type=number]').fill('1')
  await page.getByRole('button', { name: '从头全量同步' }).click()
  const startDlg = page.locator('dialog.dialog', { hasText: '开始全量场景同步' })
  await expect(startDlg).toBeVisible()
  await startDlg.getByRole('button', { name: '确认开始' }).click()
  await expect(page.locator('.scene-sync-counts')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.scene-sync-progress')).not.toContainText('%')
  await expect(page.locator('.scene-sync-progress')).not.toContainText('百分比')
  // Wait until first batch finishes at least
  await expect(page.locator('.scene-sync-progress')).toContainText(/状态：/)
  await selectScene(page, 'oven')
  await page.getByRole('tab', { name: '全量场景同步' }).click()
  const afterSceneSwitch = await page.locator('.scene-sync-progress').innerText()
  await page.locator('.scene-sync-panel').screenshot({ path: shotPath('14-15-sync-after-scene-switch') })
  evidence.L14 = { tabLabel, headerBtn, afterSceneSwitch, title: await page.locator('.scene-detail-name').innerText() }

  // Fail next batch / original cursor retry (arm after a finished or idle state if possible)
  await request.get(`${API}/qa/fail-next-sync`)
  // If already done, start a new round to hit the failure; if failed/paused, use retry
  const statusNow = await page.locator('.scene-sync-progress').innerText()
  if (await page.getByRole('button', { name: /按原游标重试本批|从上次游标继续/ }).count()) {
    await page.getByRole('button', { name: /按原游标重试本批|从上次游标继续/ }).click()
  } else {
    await page.locator('.scene-sync-panel input[type=number]').fill('1')
    await page.getByRole('button', { name: '从头全量同步' }).click()
    await page.locator('dialog.dialog', { hasText: '开始全量场景同步' }).getByRole('button', { name: '确认开始' }).click()
  }
  await expect(page.locator('.scene-sync-progress .mg-form-error')).toBeVisible({ timeout: 10_000 })
  const failedText = await page.locator('.scene-sync-progress').innerText()
  await page.locator('.scene-sync-panel').screenshot({ path: shotPath('15-sync-failed-retry') })
  const hasRetry = await page.getByRole('button', { name: '按原游标重试本批' }).count()
  evidence.L15_fail = { statusNow, failedText, hasRetry, hasPercent: /%|百分比/.test(failedText) }

  // Leave stops later batches: delay the next sync via routing, start, leave, return
  await page.unroute?.('**/api/scenes/sync').catch(() => {})
  let syncCalls = 0
  await page.route('**/api/scenes/sync', async (route) => {
    syncCalls += 1
    if (syncCalls >= 2) {
      await new Promise(resolve => setTimeout(resolve, 4000))
    }
    await route.continue()
  })
  await page.locator('.scene-sync-panel input[type=number]').fill('1')
  await page.getByRole('button', { name: '从头全量同步' }).click()
  const confirm2 = page.locator('dialog.dialog', { hasText: '开始全量场景同步' })
  if (await confirm2.count()) await confirm2.getByRole('button', { name: '确认开始' }).click()
  await page.waitForTimeout(300)
  const callsWhenLeaving = syncCalls
  await page.goto('/?view=library')
  await page.waitForTimeout(1200)
  const callsAfterLeave = syncCalls
  await page.goto('/?view=scenes&sceneTab=sync')
  await expect(page.locator('.scene-sync-panel')).toBeVisible()
  const onReturn = await page.locator('.scene-sync-progress').innerText().catch(() => '')
  const autoRunning = /状态：running/.test(onReturn)
  const continueVisible = await page.getByRole('button', { name: /从上次游标继续|按原游标重试本批/ }).count()
  await page.screenshot({ path: shotPath('15-sync-return-no-silent-resume') })
  evidence.L15_leave = { callsWhenLeaving, callsAfterLeave, syncCalls, onReturn, autoRunning, continueVisible }
  await page.unroute('**/api/scenes/sync')

  } finally {
    flush()
  }
})
