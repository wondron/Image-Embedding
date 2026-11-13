import { expect, test, type Page, type BrowserContext } from '../../frontend/node_modules/@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const STEAK_IMAGE = '00000000-0000-4000-8000-00000000000a'
const PLAIN_IMAGE = '00000000-0000-4000-8000-00000000000b'
const LONG_IMAGE = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
const OUT = path.resolve(__dirname)
const SHOT = path.join(OUT, 'screenshots')
const NETLOG: Array<Record<string, unknown>> = []
const UNHANDLED: Array<Record<string, unknown>> = []

fs.mkdirSync(SHOT, { recursive: true })

const category = (id: number, code: string, parent: number | null, name: string, isActive = true) => ({
  id, code, parent_id: parent, path: String(id), name, names: [{ lang: 'zh', name, kind: 'primary' }], cuisines: [],
  heating_group: null, sort_order: 0, is_active: isActive, image_count: 0, scene_rule_count: 0, created_at: null, updated_at: null,
})
const CATEGORIES = [
  category(1, 'dish', null, '菜品'),
  category(2, 'dish.meat', 1, '肉类'),
  category(3, 'dish.meat.steak', 2, '牛排'),
  category(4, 'dish.meat.pork', 2, '红烧肉'),
  category(5, 'dish.retired', 1, '停用类别', false),
  category(6, 'dish.meat.very-long', 2, '超长路径名称用于观察横排是否裁切或换行的类别'),
]
const SCENES = [
  { id: 1, code: 'general', name: '通用', description: null, include_all: true, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
  { id: 2, code: 'oven', name: '一体机', description: null, include_all: false, outputs_heating: true, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
  { id: 3, code: 'long-scene', name: '非常长的场景名称用来测试左栏换行而不撑破对话框', description: null, include_all: false, outputs_heating: false, is_active: true, created_at: null, member_count: 0, labels: [], rules: [] },
]
const enumItem = (name: string, values: Array<[string, string]>) => ({ name, label: name, editable: true, values: values.map(([value, label]) => ({ value, label })) })
const ENUMS = [
  enumItem('food_state', [['raw', '生'], ['cooked', '熟']]),
  enumItem('food_processing', [['sliced', '切片']]),
  enumItem('source_type', [['user_upload', '用户上传']]),
  enumItem('license', [['unknown', '未知']]),
]

type Image = Record<string, unknown>
function steakImage(): Image {
  return {
    id: STEAK_IMAGE, sha256: 'ab'.repeat(32), original_url: 'https://cdn.test/o/steak.png', thumb_url: 'https://cdn.test/t/steak.png',
    width: 640, height: 480, category_id: 3, category: '牛排', category_code: 'dish.meat.steak', label_status: 'auto',
    scenes: ['general', 'oven'], description: '煎牛排', tags: ['带骨'], source_type: 'user_upload', license: 'unknown',
    food_state: 'raw', food_processing: null, food_count: null, total_mass_g: null, water_mass_g: null, main_ingredient_mass_g: null,
    heating_group: null, payload_version: 3, created_at: '2026-11-10T08:00:00+00:00', updated_at: null,
    embeddings: [{ model_id: 1, name: 'dino', version: 'v2', collection: 'c_dino_v2', model_status: 'current', synced_version: 2, embedded_at: null }],
  }
}
function longImage(): Image {
  return {
    ...steakImage(),
    id: LONG_IMAGE,
    category_id: 6, category: '超长路径名称用于观察横排是否裁切或换行的类别', category_code: 'dish.meat.very-long',
    description: '长路径图', tags: ['超长标签名称用于观察标签控件是否对齐或裁切ABCDEF'],
    scenes: ['general', 'oven', 'long-scene'],
    original_url: 'https://cdn.test/o/long.png', thumb_url: 'https://cdn.test/t/long.png',
  }
}
function plainImage(): Image {
  return {
    ...steakImage(), id: PLAIN_IMAGE, category_id: null, category: null, category_code: null, label_status: 'unlabeled',
    description: '', tags: [], scenes: ['general'], food_state: null,
    original_url: 'https://cdn.test/o/plain.png', thumb_url: 'https://cdn.test/t/plain.png',
  }
}
function failOriginalImage(): Image {
  return { ...steakImage(), id: '00000000-0000-4000-8000-00000000000c', description: '仅原图失败', original_url: 'https://cdn.test/fail-original/x.png', thumb_url: 'https://cdn.test/t/steak.png' }
}
function failBothImage(): Image {
  return { ...steakImage(), id: '00000000-0000-4000-8000-00000000000d', description: '双源失败', original_url: 'https://cdn.test/fail-both/o.png', thumb_url: 'https://cdn.test/fail-both/t.png' }
}

const listItem = (image: Image, vectorStatus: string) => ({
  id: image.id, original_url: image.original_url, thumb_url: image.thumb_url, category_id: image.category_id, category: image.category,
  scenes: image.scenes, label_status: image.label_status, description: image.description, tags: image.tags,
  width: image.width, height: image.height, created_at: image.created_at, updated_at: image.updated_at, vector_status: vectorStatus,
})

type Store = {
  images: Map<string, Image>
  vectors: Map<string, 'stale' | 'synced' | 'missing'>
  deleted: Set<string>
  calls: { list: Array<Record<string, string>>; patches: unknown[]; pushes: string[]; reembeds: string[]; deletes: string[]; details: string[]; membershipWrites: unknown[]; unhandledApi: string[] }
}

function vectorWire(store: Store, id: string) {
  const image = store.images.get(id)!
  const status = store.vectors.get(id) ?? 'synced'
  const expected_payload = { category_id: image.category_id, scenes: image.scenes, food_state: image.food_state, label_status: image.label_status }
  if (status === 'missing') {
    return { status: 'missing', collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: false, payload: null, expected_payload, payload_matches: null, payload_version: image.payload_version, synced_version: 2, embedded_at: null }
  }
  const synced = status === 'synced'
  return {
    status, collection: 'c_dino_v2', embedding_space_id: 'dino:v2', exists: true,
    payload: { category_id: synced ? image.category_id : 3, scenes: synced ? image.scenes : ['general'], food_state: image.food_state, label_status: image.label_status },
    expected_payload, payload_matches: synced, payload_version: image.payload_version, synced_version: synced ? image.payload_version : 2, embedded_at: null,
  }
}

function defaultStore(): Store {
  const images = new Map<string, Image>([
    [STEAK_IMAGE, steakImage()],
    [PLAIN_IMAGE, plainImage()],
    [LONG_IMAGE, longImage()],
    ['00000000-0000-4000-8000-00000000000c', failOriginalImage()],
    ['00000000-0000-4000-8000-00000000000d', failBothImage()],
  ])
  const vectors = new Map<string, 'stale' | 'synced' | 'missing'>([
    [STEAK_IMAGE, 'stale'],
    [PLAIN_IMAGE, 'synced'],
    [LONG_IMAGE, 'synced'],
    ['00000000-0000-4000-8000-00000000000c', 'synced'],
    ['00000000-0000-4000-8000-00000000000d', 'missing'],
  ])
  return { images, vectors, deleted: new Set(), calls: { list: [], patches: [], pushes: [], reembeds: [], deletes: [], details: [], membershipWrites: [], unhandledApi: [] } }
}

async function installNetwork(context: BrowserContext, store: Store) {
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const rec = { method: request.method(), url: request.url(), resource: request.resourceType(), time: Date.now() }
    NETLOG.push(rec)

    // Isolated Vite origin: document, modules, HMR, sourcemaps.
    if (url.hostname === '127.0.0.1' && url.port === '57611') {
      if (url.pathname.startsWith('/api/')) {
        // fall through to API mock below (should not proxy to a real backend)
      } else {
        return route.continue()
      }
    }

    if (url.hostname === 'cdn.test') {
      if (url.pathname.includes('fail-both')) {
        return route.fulfill({ status: 404, body: 'missing' })
      }
      if (url.pathname.includes('fail-original') && url.pathname.includes('/o/')) {
        return route.fulfill({ status: 404, body: 'missing-original' })
      }
      return route.fulfill({ contentType: 'image/png', body: png })
    }

    if (url.pathname.startsWith('/api/')) {
      const pathName = url.pathname
      const method = request.method()
      if (pathName === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
      if (pathName === '/api/enums') return route.fulfill({ json: { ok: true, enums: ENUMS } })
      if (pathName === '/api/categories') return route.fulfill({ json: { ok: true, categories: CATEGORIES, heating_groups: [], root_codes: ['dish'] } })
      if (pathName === '/api/scenes') return route.fulfill({ json: { ok: true, scenes: SCENES, default_scene: 'general' } })
      if (pathName === '/api/images' && method === 'GET') {
        store.calls.list.push(Object.fromEntries(url.searchParams))
        const items = [...store.images.values()].filter(img => !store.deleted.has(String(img.id))).map(img => listItem(img, store.vectors.get(String(img.id)) ?? 'synced'))
        return route.fulfill({ json: { ok: true, trace_id: 'qa-v03', scene: 'general', page: Number(url.searchParams.get('page') || 1), page_size: Number(url.searchParams.get('page_size') || 24), total: items.length, items } })
      }
      const membershipGet = pathName.match(/^\/api\/images\/([^/]+)\/scene-memberships$/)
      if (membershipGet && method === 'GET') {
        const id = membershipGet[1]
        const image = store.images.get(id) ?? steakImage()
        const scenes = (image.scenes as string[]) || []
        return route.fulfill({ json: { ok: true, image_id: id, payload_version: image.payload_version, scenes, memberships: SCENES.map(scene => ({
          scene_id: scene.id, scene_code: scene.code, is_active: scene.is_active, include_all: scene.include_all,
          is_member: scenes.includes(scene.code), origin: scene.include_all ? 'implicit' : (scenes.includes(scene.code) ? 'rule' : 'none'), manual_override: null,
        })) } })
      }
      const membershipPut = pathName.match(/^\/api\/images\/([^/]+)\/scene-memberships\/(\d+)$/)
      if (membershipPut && (method === 'PUT' || method === 'POST' || method === 'PATCH')) {
        const body = request.postDataJSON() as Record<string, unknown>
        store.calls.membershipWrites.push({ path: pathName, method, body })
        return route.fulfill({ json: { ok: true, image_id: membershipPut[1], sync_required: false, scenes: ['general'] } })
      }
      const reembed = pathName.match(/^\/api\/images\/([^/]+)\/reembed$/)
      if (reembed && method === 'POST') {
        store.calls.reembeds.push(reembed[1])
        store.vectors.set(reembed[1], 'synced')
        return route.fulfill({ json: { ok: true, image_id: reembed[1], payload_version: store.images.get(reembed[1])?.payload_version, vector: vectorWire(store, reembed[1]) } })
      }
      const sync = pathName.match(/^\/api\/images\/([^/]+)\/vector-sync$/)
      if (sync && method === 'POST') {
        store.calls.pushes.push(sync[1])
        store.vectors.set(sync[1], 'synced')
        const image = store.images.get(sync[1])
        return route.fulfill({ json: { ok: true, image_id: sync[1], payload_version: image?.payload_version, vector: vectorWire(store, sync[1]), vector_sync: 'pushed', sync_required: false } })
      }
      const one = pathName.match(/^\/api\/images\/([^/]+)$/)
      if (one) {
        const id = one[1]
        if (store.deleted.has(id) || !store.images.has(id)) return route.fulfill({ status: 404, json: { ok: false, error: 'IMAGE_NOT_FOUND', detail: 'gone', trace_id: 'qa-v03' } })
        if (method === 'GET') {
          store.calls.details.push(id)
          return route.fulfill({ json: { ok: true, image: store.images.get(id), vector: vectorWire(store, id) } })
        }
        if (method === 'PATCH') {
          const patch = request.postDataJSON() as Record<string, unknown>
          store.calls.patches.push({ id, patch })
          const current = store.images.get(id)!
          const next = { ...current, ...patch, payload_version: Number(current.payload_version) + 1 }
          if ('category_id' in patch) {
            const picked = CATEGORIES.find(item => item.id === patch.category_id)
            Object.assign(next, { category: picked?.name ?? null, category_code: picked?.code ?? null })
          }
          store.images.set(id, next)
          store.vectors.set(id, 'synced')
          return route.fulfill({ json: { ok: true, image: next, vector: vectorWire(store, id), vector_sync: 'pushed', sync_required: false } })
        }
        if (method === 'DELETE') {
          store.calls.deletes.push(id)
          store.deleted.add(id)
          return route.fulfill({ json: { ok: true, image_id: id, deleted_at: '2026-11-12T20:00:00+00:00', cleanup: 'done' } })
        }
      }
      store.calls.unhandledApi.push(`${method} ${pathName}`)
      UNHANDLED.push({ ...rec, kind: 'unhandled-api' })
      return route.fulfill({ status: 404, json: { ok: false, error: 'QA_UNHANDLED', detail: `${method} ${pathName}`, trace_id: 'qa-v03' } })
    }

    UNHANDLED.push({ ...rec, kind: 'blocked-non-vite' })
    return route.abort('blockedbyclient')
  })
}

async function openLibrary(page: Page) {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: '展开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: /^图片数据/ }).click()
}

async function openEditor(page: Page, label = '标注 煎牛排') {
  await page.getByRole('button', { name: label }).click()
  const dialog = page.getByRole('dialog', { name: '图片标注' })
  await expect(dialog.locator('.ie')).toBeVisible()
  return dialog
}

async function shot(page: Page, name: string) {
  const dest = path.join(SHOT, `${name}.png`)
  await page.screenshot({ path: dest, fullPage: true, animations: 'disabled' })
  return dest
}

async function measureCategoryLabel(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null) => {
      if (!el) return null
      const r = (el as HTMLElement).getBoundingClientRect()
      const cs = getComputedStyle(el)
      return {
        x: r.x, y: r.y, width: r.width, height: r.height, top: r.top, right: r.right, bottom: r.bottom, left: r.left,
        scrollWidth: (el as HTMLElement).scrollWidth, scrollHeight: (el as HTMLElement).scrollHeight,
        clientWidth: (el as HTMLElement).clientWidth, clientHeight: (el as HTMLElement).clientHeight,
        overflow: `${cs.overflowX}/${cs.overflowY}`,
        display: cs.display,
        gridTemplateColumns: cs.gridTemplateColumns,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        fontFamily: cs.fontFamily,
        color: cs.color,
        position: cs.position,
        visibility: cs.visibility,
      }
    }
    const contentBox = (el: Element | null) => {
      if (!el) return null
      const cs = getComputedStyle(el)
      const w = (el as HTMLElement).clientWidth
      const h = (el as HTMLElement).clientHeight
      const pl = parseFloat(cs.paddingLeft) || 0
      const pr = parseFloat(cs.paddingRight) || 0
      const pt = parseFloat(cs.paddingTop) || 0
      const pb = parseFloat(cs.paddingBottom) || 0
      return { clientWidth: w, clientHeight: h, paddingLeft: pl, paddingRight: pr, paddingTop: pt, paddingBottom: pb, contentWidth: w - pl - pr, contentHeight: h - pt - pb }
    }
    const clippedByAncestor = (el: Element | null) => {
      if (!el) return null
      const r = (el as HTMLElement).getBoundingClientRect()
      const hits: Array<Record<string, unknown>> = []
      let p: HTMLElement | null = el.parentElement
      while (p) {
        const cs = getComputedStyle(p)
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible' || cs.overflow !== 'visible') {
          const pr = p.getBoundingClientRect()
          const dxR = r.right - pr.right
          const dxL = pr.left - r.left
          const dyB = r.bottom - pr.bottom
          const dyT = pr.top - r.top
          if (dxR > 0.5 || dxL > 0.5 || dyB > 0.5 || dyT > 0.5) {
            hits.push({
              parent: `${p.tagName.toLowerCase()}.${(p.className || '').toString().toString()}`.slice(0, 180),
              overflow: `${cs.overflowX}/${cs.overflowY}`,
              cutRight: Math.max(0, dxR), cutLeft: Math.max(0, dxL), cutBottom: Math.max(0, dyB), cutTop: Math.max(0, dyT),
            })
          }
        }
        p = p.parentElement
      }
      return hits
    }
    const selfOverflow = (el: Element | null) => {
      if (!el) return null
      const h = el as HTMLElement
      return { overflowX: h.scrollWidth - h.clientWidth, overflowY: h.scrollHeight - h.clientHeight }
    }
    const dialog = document.querySelector('dialog[open]') as HTMLElement | null
    const ie = document.querySelector('.ie')
    const scroll = document.querySelector('.ie-scroll')
    const fields = document.querySelector('.ie-category-fields')
    const cp = document.querySelector('.ie-category-fields .cp')
    const selected = document.querySelector('.ie-category-fields .cp-selected')
    const selectedStrong = document.querySelector('.ie-category-fields .cp-selected strong')
    const selectedSmall = document.querySelector('.ie-category-fields .cp-selected small')
    const selectedCode = document.querySelector('.ie-category-fields .cp-selected code')
    const combo = document.querySelector('.ie-category-fields .cp-combo')
    const choice = document.querySelector('.ie-category-fields .ia-choice')
    const legend = document.querySelector('.ie-category-fields .ia-choice legend')
    const radios = [...document.querySelectorAll('.ie-category-fields .ia-choice label')]
    const hint = document.querySelector('.ie-category-fields .ia-choice small')
    const textFields = document.querySelector('.ie-text-fields')
    const tagField = document.querySelector('.ie-text-fields .ia-field')
    const descField = document.querySelectorAll('.ie-text-fields .ia-field')[1] || null
    const railFacts = [...document.querySelectorAll('.ie-rail .ie-meta > div')].map(div => {
      const dt = div.querySelector('dt')?.textContent?.trim() || ''
      const dd = div.querySelector('dd')?.textContent?.trim() || ''
      return { dt, dd }
    })
    const cpBox = box(cp)
    const choiceBox = box(choice)
    const horizontal = !!(cpBox && choiceBox && Math.abs(cpBox.y - choiceBox.y) < Math.min(cpBox.height, choiceBox.height) * 0.6 && choiceBox.x >= cpBox.x + cpBox.width - 2)
    const stacked = !!(cpBox && choiceBox && choiceBox.y >= cpBox.y + cpBox.height - 2)
    const vp = { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio }
    return {
      viewport: vp,
      dialog: box(dialog),
      dialogContent: contentBox(dialog),
      ieScroll: box(scroll),
      ieScrollContent: contentBox(scroll),
      ieMain: box(document.querySelector('.ie-main')),
      ieRail: box(document.querySelector('.ie-rail')),
      ieFigure: box(document.querySelector('.ie-figure')),
      ieBar: box(document.querySelector('.ie-bar')),
      categoryFields: box(fields),
      categoryPicker: cpBox,
      selectedCard: box(selected),
      selectedStrong: { ...box(selectedStrong) || {}, text: selectedStrong?.textContent },
      selectedPath: { ...box(selectedSmall) || {}, text: selectedSmall?.textContent },
      selectedCode: { ...box(selectedCode) || {}, text: selectedCode?.textContent },
      combo: box(combo),
      comboInputValue: (document.querySelector('.ie-category-fields .cp-combo input') as HTMLInputElement | null)?.placeholder || null,
      choice: choiceBox,
      legend: { ...box(legend) || {}, text: legend?.textContent },
      radios: radios.map(el => ({ text: el.textContent, box: box(el), checked: !!(el.querySelector('input') as HTMLInputElement | null)?.checked })),
      choiceHint: { ...box(hint) || {}, text: hint?.textContent },
      textFields: box(textFields),
      tagField: box(tagField),
      descField: box(descField),
      alignment: {
        horizontal,
        stacked,
        labelTopDelta: (box(legend)?.y ?? 0) - (box(document.querySelector('.ie-category-fields .cp-label'))?.y ?? 0),
        selectedVsRadiosTopDelta: (box(radios[0])?.y ?? 0) - (box(selected)?.y ?? 0),
        tagVsDescTopDelta: (box(descField)?.y ?? 0) - (box(tagField)?.y ?? 0),
        tagVsDescLeftDelta: (box(descField)?.x ?? 0) - (box(tagField)?.x ?? 0),
        gapBetweenCpAndChoice: (choiceBox && cpBox) ? (horizontal ? choiceBox.x - (cpBox.x + cpBox.width) : choiceBox.y - (cpBox.y + cpBox.height)) : null,
      },
      clipping: {
        selectedCard: clippedByAncestor(selected),
        selectedStrong: clippedByAncestor(selectedStrong),
        selectedPath: clippedByAncestor(selectedSmall),
        combo: clippedByAncestor(combo),
        choice: clippedByAncestor(choice),
        legend: clippedByAncestor(legend),
        radios: radios.map(el => clippedByAncestor(el)),
        hint: clippedByAncestor(hint),
        tagField: clippedByAncestor(tagField),
      },
      selfOverflow: {
        selectedCard: selfOverflow(selected),
        selectedStrong: selfOverflow(selectedStrong),
        selectedPath: selfOverflow(selectedSmall),
        combo: selfOverflow(combo),
        choice: selfOverflow(choice),
        hint: selfOverflow(hint),
        fields: selfOverflow(fields),
        scroll: selfOverflow(scroll),
        dialog: selfOverflow(dialog),
        ie: selfOverflow(ie),
      },
      savedRail: railFacts,
      headingCategory: document.querySelector('#\\:r0\\:-category-title, .ie-form .ia-section h3')?.textContent || document.querySelector('.ie-form .ia-section h3')?.textContent || null,
      changesTitle: (document.querySelector('.ie-changes') as HTMLElement | null)?.getAttribute('title'),
      changesText: (document.querySelector('.ie-changes') as HTMLElement | null)?.textContent,
      foodOpen: (document.querySelector('details.ia-collapsible') as HTMLDetailsElement | null)?.open ?? null,
    }
  })
}

function writeJson(name: string, data: unknown) {
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(data, null, 2), 'utf8')
}

const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  { name: '1280x900', width: 1280, height: 900 },
  { name: '950x760', width: 950, height: 760 },
  { name: '900x760', width: 900, height: 760 },
  { name: '900x640', width: 900, height: 640 },
  { name: '900x639', width: 900, height: 639 },
  { name: '899x760', width: 899, height: 760 },
  { name: '700x760', width: 700, height: 760 },
  { name: '640x400', width: 640, height: 400 },
  { name: '500x400', width: 500, height: 400 },
  { name: '400x400', width: 400, height: 400 },
  { name: '360x400', width: 360, height: 400 },
]

test.describe.configure({ mode: 'serial' })

test.describe('image-data-detail v0.3 isolated acceptance', () => {
  let store: Store

  test.beforeEach(async ({ context }) => {
    store = defaultStore()
    NETLOG.length = 0
    UNHANDLED.length = 0
    await installNetwork(context, store)
  })

  test.afterEach(async () => {
    writeJson('network-log-last.json', { count: NETLOG.length, unhandled: UNHANDLED, sample: NETLOG.slice(0, 40), unhandledApi: store?.calls.unhandledApi })
  })

  test('P0: 类别与标注状态横排/裁切/对齐与选中值', async ({ page }) => {
    const results: unknown[] = []
    await page.setViewportSize({ width: 1280, height: 900 })
    await openLibrary(page)
    const dialog = await openEditor(page)

    async function capture(tag: string) {
      await expect(dialog.locator('.ie-category-fields')).toBeVisible()
      const measured = await measureCategoryLabel(page)
      const shotPath = await shot(page, `P0-${tag}`)
      const row = { tag, screenshot: shotPath, measured }
      results.push(row)
      return measured as any
    }

    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.waitForTimeout(200)
      await capture(vp.name)
    }

    // Hunt content-box 519 and 520 by adjusting viewport width at height 760.
    const hunt: unknown[] = []
    for (let width = 860; width <= 1100; width += 2) {
      await page.setViewportSize({ width, height: 760 })
      const measured = await measureCategoryLabel(page) as any
      const cw = measured.ieScrollContent?.contentWidth ?? null
      hunt.push({ width, height: 760, contentWidth: cw, horizontal: measured.alignment?.horizontal, stacked: measured.alignment?.stacked, grid: measured.categoryFields?.gridTemplateColumns })
    }
    const around520 = (hunt as any[]).filter(row => typeof row.contentWidth === 'number' && Math.abs(row.contentWidth - 520) <= 8)
    const closest520 = (hunt as any[]).reduce((best, row) => {
      if (typeof row.contentWidth !== 'number') return best
      if (!best || Math.abs(row.contentWidth - 520) < Math.abs(best.contentWidth - 520)) return row
      return best
    }, null as any)
    const closest519 = (hunt as any[]).reduce((best, row) => {
      if (typeof row.contentWidth !== 'number') return best
      if (!best || Math.abs(row.contentWidth - 519) < Math.abs(best.contentWidth - 519)) return row
      return best
    }, null as any)

    async function captureExact(target: number, found: any, tag: string) {
      if (!found) return null
      await page.setViewportSize({ width: found.width, height: 760 })
      await page.waitForTimeout(200)
      const measured = await capture(`${tag}-w${found.width}`)
      return { target, foundWidth: found.width, measuredContent: (measured as any).ieScrollContent, alignment: (measured as any).alignment, grid: (measured as any).categoryFields?.gridTemplateColumns }
    }
    const at520 = await captureExact(520, closest520, 'content520')
    const at519 = await captureExact(519, closest519, 'content519')

    // Selected-value correctness + draft vs saved snapshot, at a wide horizontal viewport.
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.waitForTimeout(200)
    const before = await capture('1280x900-values-before')
    const leftCategoryBefore = (before as any).savedRail.find((row: any) => row.dt === '类别')?.dd
    const leftStatusBefore = (before as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd
    await dialog.getByRole('combobox', { name: '类别' }).fill('红烧')
    await dialog.getByRole('option', { name: /^红烧肉/ }).click()
    await expect(dialog.getByRole('radio', { name: '已核验' })).toBeChecked()
    const afterPick = await capture('1280x900-after-pick-hongshao')
    const leftCategoryAfter = (afterPick as any).savedRail.find((row: any) => row.dt === '类别')?.dd
    const leftStatusAfter = (afterPick as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd
    await dialog.getByRole('radio', { name: '自动标注' }).check()
    const afterRadio = await capture('1280x900-after-radio-auto')

    // Long path image at the same wide viewport (new dialog).
    await dialog.getByRole('button', { name: '撤销修改' }).click()
    await dialog.getByRole('button', { name: '关闭弹窗' }).click()
    const longDialog = await openEditor(page, '标注 长路径图')
    await expect(longDialog.locator('.ie-category-fields')).toBeVisible()
    const longWide = await (async () => {
      const measured = await measureCategoryLabel(page)
      const shotPath = await shot(page, 'P0-1280x900-long-path')
      results.push({ tag: '1280x900-long-path', screenshot: shotPath, measured })
      return measured as any
    })()
    await page.setViewportSize({ width: 700, height: 760 })
    await page.waitForTimeout(200)
    const longNarrow = await (async () => {
      const measured = await measureCategoryLabel(page)
      const shotPath = await shot(page, 'P0-700x760-long-path')
      results.push({ tag: '700x760-long-path', screenshot: shotPath, measured })
      return measured as any
    })()

    const summary = {
      fixtureNote: '全部为隔离测试夹具数据，不是真实业务库。主图为煎牛排（类别 菜品/肉类/牛排，标注状态 自动标注）；另有超长类别路径图。',
      viewportMatrix: results,
      contentBoxHunt: { around520, closest519, closest520, at519, at520, rawCount: hunt.length },
      valueChecks: {
        before: {
          leftCategory: leftCategoryBefore,
          leftStatus: leftStatusBefore,
          selectedName: (before as any).selectedStrong?.text,
          selectedPath: (before as any).selectedPath?.text,
          selectedCode: (before as any).selectedCode?.text,
          radios: (before as any).radios,
        },
        afterPick: {
          leftCategory: leftCategoryAfter,
          leftStatus: leftStatusAfter,
          selectedName: (afterPick as any).selectedStrong?.text,
          selectedPath: (afterPick as any).selectedPath?.text,
          selectedCode: (afterPick as any).selectedCode?.text,
          radios: (afterPick as any).radios,
          leftUnchangedCategory: leftCategoryAfter === leftCategoryBefore,
          leftUnchangedStatus: leftStatusAfter === leftStatusBefore,
        },
        afterRadio: {
          leftStatus: (afterRadio as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd,
          radios: (afterRadio as any).radios,
          leftStillAuto: (afterRadio as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd === '自动标注',
        },
        longPath: {
          wide: { selectedName: longWide.selectedStrong?.text, selectedPath: longWide.selectedPath?.text, clipping: longWide.clipping, selfOverflow: longWide.selfOverflow, alignment: longWide.alignment },
          narrow: { selectedName: longNarrow.selectedStrong?.text, selectedPath: longNarrow.selectedPath?.text, clipping: longNarrow.clipping, selfOverflow: longNarrow.selfOverflow, alignment: longNarrow.alignment },
        },
      },
      unhandled: UNHANDLED,
      apiUnhandled: store.calls.unhandledApi,
    }
    writeJson('P0-category-label-measurements.json', summary)
  })

  test('A-D: v0.3 17项真实窗口测量与行为（隔离夹具）', async ({ page }) => {
    const evidence: Record<string, unknown> = { items: {}, shots: [], networkUnhandled: [] }
    const record = (id: number, data: unknown) => { (evidence.items as any)[id] = data }

    await page.setViewportSize({ width: 1280, height: 900 })
    await openLibrary(page)
    const dialog = await openEditor(page)

    // Item 1: accessible name, isolate other surfaces later
    const accName = await dialog.evaluate(el => el.getAttribute('aria-labelledby') && (document.getElementById(el.getAttribute('aria-labelledby')!)?.textContent || ''))
    const titleText = await dialog.locator('.dialog-heading h2').innerText()
    await shot(page, 'I01-library-dialog')
    record(1, { titleText, accName, passHint: titleText === '图片标注' })

    // Item 5,6,7,9 at 1280x900 and 900x760
    async function layoutPack(tag: string) {
      const pack = await page.evaluate(() => {
        const box = (sel: string) => {
          const el = document.querySelector(sel) as HTMLElement | null
          if (!el) return null
          const r = el.getBoundingClientRect()
          const cs = getComputedStyle(el)
          return { x: r.x, y: r.y, w: r.width, h: r.height, overflow: `${cs.overflowX}/${cs.overflowY}`, position: cs.position, display: cs.display, gridTemplateColumns: cs.gridTemplateColumns, inlineSize: cs.inlineSize, blockSize: cs.blockSize, width: cs.width, height: cs.height }
        }
        const content = (sel: string) => {
          const el = document.querySelector(sel) as HTMLElement | null
          if (!el) return null
          const cs = getComputedStyle(el)
          const pl = parseFloat(cs.paddingLeft) || 0, pr = parseFloat(cs.paddingRight) || 0, pt = parseFloat(cs.paddingTop) || 0, pb = parseFloat(cs.paddingBottom) || 0
          return { clientWidth: el.clientWidth, clientHeight: el.clientHeight, contentWidth: el.clientWidth - pl - pr, contentHeight: el.clientHeight - pt - pb, padding: [pt, pr, pb, pl], scrollbarGutter: cs.scrollbarGutter, borderBoxW: el.getBoundingClientRect().width, borderBoxH: el.getBoundingClientRect().height }
        }
        const groups = [...document.querySelectorAll('.ie-rail .ie-meta-group')].map(sec => ({
          label: sec.getAttribute('aria-label'),
          caption: sec.querySelector('.ie-meta-caption')?.textContent,
          fields: [...sec.querySelectorAll('.ie-meta > div')].map(div => (div.querySelector('dt')?.textContent || '').trim()),
        }))
        const factsInRail = !!document.querySelector('.ie-rail .ie-facts')
        const factsInScroll = !!document.querySelector('.ie-scroll .ie-facts')
        const vectorInScroll = !!document.querySelector('.ie-scroll .ie-vector')
        const vectorInRail = !!document.querySelector('.ie-rail .ie-vector')
        const formInScroll = !!document.querySelector('.ie-scroll .ie-form')
        const membersInScroll = !!document.querySelector('.ie-scroll .ie-memberships')
        const figureSticky = getComputedStyle(document.querySelector('.ie-figure')!).position
        const railSticky = getComputedStyle(document.querySelector('.ie-rail')!).position
        const dtCols = getComputedStyle(document.querySelector('.ie-rail .ie-meta > div')!).gridTemplateColumns
        return {
          viewport: { w: innerWidth, h: innerHeight },
          dialog: box('dialog[open]'),
          dialogContent: content('dialog[open]'),
          main: box('.ie-main'),
          rail: box('.ie-rail'),
          figure: box('.ie-figure'),
          scroll: box('.ie-scroll'),
          scrollContent: content('.ie-scroll'),
          bar: box('.ie-bar'),
          facts: box('.ie-facts'),
          groups, factsInRail, factsInScroll, vectorInScroll, vectorInRail, formInScroll, membersInScroll, figureSticky, railSticky, dtCols,
        }
      })
      const shotPath = await shot(page, tag)
      return { tag, shotPath, pack }
    }

    const at1280 = await layoutPack('I05-09-1280x900')
    await page.setViewportSize({ width: 900, height: 760 })
    await page.waitForTimeout(200)
    const at900x760 = await layoutPack('I05-09-900x760')

    // Item 7: independent scroll, not sticky, can reach ID
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.waitForTimeout(200)
    const scrollEvidence = await page.evaluate(async () => {
      const rail = document.querySelector('.ie-rail') as HTMLElement
      const scroll = document.querySelector('.ie-scroll') as HTMLElement
      const main = document.querySelector('.ie-main') as HTMLElement
      const figure = document.querySelector('.ie-figure') as HTMLElement
      const idRow = [...document.querySelectorAll('.ie-rail .ie-meta > div')].find(div => div.querySelector('dt')?.textContent?.trim() === '图片 ID') as HTMLElement
      const before = {
        railTop: rail.scrollTop, scrollTop: scroll.scrollTop, mainTop: main.scrollTop,
        figureY: figure.getBoundingClientRect().y, idY: idRow?.getBoundingClientRect().y,
        railOverflow: `${getComputedStyle(rail).overflowY}`, scrollOverflow: `${getComputedStyle(scroll).overflowY}`, mainOverflow: `${getComputedStyle(main).overflowY}`,
        figurePosition: getComputedStyle(figure).position,
        railCanScroll: rail.scrollHeight - rail.clientHeight,
        scrollCanScroll: scroll.scrollHeight - scroll.clientHeight,
      }
      rail.scrollTop = rail.scrollHeight
      const afterRail = {
        railTop: rail.scrollTop, scrollTop: scroll.scrollTop, figureY: figure.getBoundingClientRect().y,
        idVisibleTop: idRow?.getBoundingClientRect().top, idVisibleBottom: idRow?.getBoundingClientRect().bottom,
        railBox: rail.getBoundingClientRect(), idText: idRow?.querySelector('dd')?.textContent,
        figureCoversId: figure.getBoundingClientRect().bottom > (idRow?.getBoundingClientRect().top ?? 0) + 1 && getComputedStyle(figure).position === 'sticky',
      }
      const idInRailView = !!idRow && idRow.getBoundingClientRect().bottom <= rail.getBoundingClientRect().bottom + 1 && idRow.getBoundingClientRect().top >= rail.getBoundingClientRect().top - 1
      scroll.scrollTop = scroll.scrollHeight
      const afterScroll = { railTop: rail.scrollTop, scrollTop: scroll.scrollTop, figureY: figure.getBoundingClientRect().y, idY: idRow?.getBoundingClientRect().y }
      // reset
      rail.scrollTop = 0
      scroll.scrollTop = 0
      return { before, afterRail, afterScroll, idInRailView }
    })
    await page.evaluate(() => { const rail = document.querySelector('.ie-rail') as HTMLElement; rail.scrollTop = rail.scrollHeight })
    await shot(page, 'I07-rail-scrolled-to-id')
    await page.evaluate(() => { const rail = document.querySelector('.ie-rail') as HTMLElement; rail.scrollTop = 0 })
    record(7, scrollEvidence)

    record(5, { at1280, at900x760 })
    record(6, {
      at1280: {
        factsInRail: (at1280 as any).pack.factsInRail,
        factsInScroll: (at1280 as any).pack.factsInScroll,
        vectorInScroll: (at1280 as any).pack.vectorInScroll,
        vectorInRail: (at1280 as any).pack.vectorInRail,
        formInScroll: (at1280 as any).pack.formInScroll,
        membersInScroll: (at1280 as any).pack.membersInScroll,
      },
    })
    record(9, { at1280: (at1280 as any).pack, at900x760: (at900x760 as any).pack })

    // Item 10: 900x640 still two columns, 200x200 preview
    await page.setViewportSize({ width: 900, height: 640 })
    await page.waitForTimeout(200)
    const at900x640 = await layoutPack('I10-900x640')
    record(10, at900x640)

    // Item 11: 900x639 and 899x760 single column
    await page.setViewportSize({ width: 900, height: 639 })
    await page.waitForTimeout(200)
    const at900x639 = await layoutPack('I11-900x639')
    await page.setViewportSize({ width: 899, height: 760 })
    await page.waitForTimeout(200)
    const at899x760 = await layoutPack('I11-899x760')
    record(11, { at900x639, at899x760 })

    // Item 12: <=500
    await page.setViewportSize({ width: 500, height: 400 })
    await page.waitForTimeout(200)
    const at500 = await layoutPack('I12-500x400')
    await page.setViewportSize({ width: 360, height: 400 })
    await page.waitForTimeout(200)
    const at360 = await layoutPack('I12-360x400')
    record(12, { at500, at360 })

    // Item 13: footer at short content vs confirm-delete at 400 height
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.waitForTimeout(200)
    const shortFooter = await page.evaluate(() => {
      const dialog = document.querySelector('dialog[open]') as HTMLElement
      const bar = document.querySelector('.ie-bar') as HTMLElement
      const d = dialog.getBoundingClientRect()
      const b = bar.getBoundingClientRect()
      const cs = getComputedStyle(bar)
      return { dialogBottom: d.bottom, barBottom: b.bottom, gap: d.bottom - b.bottom, barHeight: b.height, maxBlock: cs.maxBlockSize, overflow: `${cs.overflowX}/${cs.overflowY}`, position: cs.position }
    })
    await dialog.getByRole('button', { name: '删除图片' }).click()
    await expect(dialog.getByRole('alertdialog', { name: '确认删除' })).toBeVisible()
    await shot(page, 'I13-confirm-1280x900')
    const confirmWide = await page.evaluate(() => {
      const bar = document.querySelector('.ie-bar') as HTMLElement
      const btn = [...bar.querySelectorAll('button')].find(b => b.textContent?.includes('确认删除')) as HTMLElement
      const cs = getComputedStyle(bar)
      const b = bar.getBoundingClientRect()
      return { barHeight: b.height, maxBlock: cs.maxBlockSize, overflow: `${cs.overflowX}/${cs.overflowY}`, canScroll: bar.scrollHeight - bar.clientHeight, confirmVisible: !!btn && btn.getBoundingClientRect().bottom <= b.bottom + 1, confirmBox: btn?.getBoundingClientRect() }
    })
    await page.setViewportSize({ width: 640, height: 400 })
    await page.waitForTimeout(200)
    const confirm400 = await page.evaluate(() => {
      const dialog = document.querySelector('dialog[open]') as HTMLElement
      const bar = document.querySelector('.ie-bar') as HTMLElement
      const heading = document.querySelector('dialog[open] .dialog-heading') as HTMLElement
      const btn = [...bar.querySelectorAll('button')].find(b => b.textContent?.includes('确认删除')) as HTMLElement
      bar.scrollTop = bar.scrollHeight
      const cs = getComputedStyle(bar)
      return {
        viewport: { w: innerWidth, h: innerHeight },
        headingVisible: heading.getBoundingClientRect().bottom > 0,
        dialog: dialog.getBoundingClientRect(),
        bar: bar.getBoundingClientRect(),
        barHeight: bar.getBoundingClientRect().height,
        maxBlock: cs.maxBlockSize,
        canScroll: bar.scrollHeight - bar.clientHeight,
        confirmBox: btn?.getBoundingClientRect(),
        confirmReachable: !!btn && btn.getBoundingClientRect().height > 0 && btn.getBoundingClientRect().bottom <= bar.getBoundingClientRect().bottom + 1,
        windowScroll: { doc: document.documentElement.scrollTop, body: document.body.scrollTop, docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth },
      }
    })
    await shot(page, 'I13-confirm-640x400')
    await dialog.getByRole('button', { name: '取消' }).click()
    record(13, { shortFooter, confirmWide, confirm400 })

    // Item 8 + 2 + 3 + 14 behavior on steak at 1280
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.waitForTimeout(200)
    const foodOpenFilled = await dialog.locator('details.ia-collapsible').evaluate(el => (el as HTMLDetailsElement).open)
    const coreVisible = {
      categoryHeading: await dialog.getByRole('heading', { name: '类别与标注状态' }).isVisible(),
      tagsHeading: await dialog.getByRole('heading', { name: '标签与描述' }).isVisible(),
      tagInput: await dialog.getByLabel('标签', { exact: true }).isVisible(),
      description: await dialog.getByLabel('描述', { exact: true }).isVisible(),
      members: await dialog.getByRole('heading', { name: '场景成员' }).isVisible(),
      vector: await dialog.locator('.ie-vector').isVisible(),
    }
    const textareaRows = await dialog.getByLabel('描述', { exact: true }).getAttribute('rows')
    record(8, { foodOpenFilled, coreVisible, textareaRows })

    // title=changes and dirty close
    const titleIdle = await dialog.locator('.ie-changes').getAttribute('title')
    const textIdle = (await dialog.locator('.ie-changes').innerText()).trim()
    await dialog.getByLabel('描述', { exact: true }).fill('改过的描述')
    const titleDirty = await dialog.locator('.ie-changes').getAttribute('title')
    const textDirty = (await dialog.locator('.ie-changes').innerText()).trim()
    const closeDisabled = await dialog.getByRole('button', { name: '关闭弹窗' }).isDisabled()
    await shot(page, 'I03-dirty-title')
    await dialog.getByRole('button', { name: '撤销修改' }).click()
    const titleAfterUndo = await dialog.locator('.ie-changes').getAttribute('title')
    const closeAfterUndo = await dialog.getByRole('button', { name: '关闭弹窗' }).isEnabled()
    record(3, { titleIdle, textIdle, titleDirty, textDirty, closeDisabled, titleAfterUndo, closeAfterUndo })

    // Item 2: vector push, save, undo already, delete later on a copy? We'll use steak carefully.
    await expect(dialog.locator('.ie-vector')).toContainText('待同步')
    await dialog.getByRole('button', { name: '按数据库同步到向量库' }).click()
    await expect(dialog.locator('.ie-vector')).toContainText('已同步')
    const pushes = store.calls.pushes.slice()
    await dialog.getByLabel('描述', { exact: true }).fill('验收保存描述')
    await dialog.getByRole('button', { name: '保存修改' }).click()
    // toast may appear
    await page.waitForTimeout(400)
    const patches = store.calls.patches.slice()
    record(2, { pushes, patches, deletesSoFar: store.calls.deletes.slice(), vectorAfterPush: true })

    // Item 14: already have hunt in P0; re-measure here too at current viewport
    const item14 = await measureCategoryLabel(page)
    record(14, { at1280: { horizontal: (item14 as any).alignment?.horizontal, contentWidth: (item14 as any).ieScrollContent?.contentWidth, grid: (item14 as any).categoryFields?.gridTemplateColumns, textGrid: (item14 as any).textFields?.gridTemplateColumns } })

    // Item 15 typography inside dialog, then scene management page
    const typoIe = await page.evaluate(() => {
      const pick = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null
        if (!el) return null
        const cs = getComputedStyle(el)
        return { fontSize: cs.fontSize, fontWeight: cs.fontWeight, lineHeight: cs.lineHeight, fontFamily: cs.fontFamily, color: cs.color, text: el.textContent?.trim().slice(0, 80) }
      }
      return {
        factsH3: pick('.ie-facts > h3'),
        categoryH3: pick('.ie-form .ia-section h3'),
        sceneH4: pick('.ie .scene-membership-head h4'),
        sceneTh: pick('.ie-memberships .scene-table th'),
        sceneTd: pick('.ie-memberships .scene-table td'),
        textBtn: pick('.ie .scene-membership-panel .text-button'),
        help: pick('.ie .scene-membership-panel .mg-help'),
      }
    })
    await shot(page, 'I15-typography-in-dialog')
    record(15, { typoIe })

    // Item 17 long path already captured in P0; also measure wrap here if long dialog not open
    const wrap = await page.evaluate(() => {
      const dialog = document.querySelector('dialog[open]') as HTMLElement
      const id = [...document.querySelectorAll('.ie-rail dd.monospace')].at(0) as HTMLElement | undefined
      const cat = [...document.querySelectorAll('.ie-rail .ie-meta > div')].find(div => div.querySelector('dt')?.textContent?.trim() === '类别')?.querySelector('dd') as HTMLElement
      return {
        dialogW: dialog.getBoundingClientRect().width,
        overflowDoc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        id: id ? { text: id.textContent, w: id.getBoundingClientRect().width, scroll: id.scrollWidth, wrap: getComputedStyle(id).overflowWrap } : null,
        cat: cat ? { text: cat.textContent, w: cat.getBoundingClientRect().width, scroll: cat.scrollWidth, wrap: getComputedStyle(cat).overflowWrap } : null,
      }
    })
    record(17, wrap)

    writeJson('v03-items-partial.json', evidence)
  })

  test('I15 scene page + I01/I04 isolation + I16 missing image + I08 empty food + delete', async ({ page }) => {
    const ev: Record<string, unknown> = {}
    await page.setViewportSize({ width: 1280, height: 900 })
    await openLibrary(page)

    // Empty-food image: 未标注 / no food fields
    const plainDlg = await openEditor(page, '标注 未标注')
    const foodOpenEmpty = await plainDlg.locator('details.ia-collapsible').evaluate(el => (el as HTMLDetailsElement).open)
    const choiceMissing = await plainDlg.locator('.ie-category-fields .ia-choice').count()
    await shot(page, 'I08-plain-food-closed')
    ev.foodOpenEmpty = foodOpenEmpty
    ev.plainHasNoStatusBecauseNoCategory = choiceMissing === 0
    await plainDlg.getByRole('button', { name: '关闭弹窗' }).click()

    // I16 missing images
    const both = await openEditor(page, '标注 双源失败')
    await page.waitForTimeout(500)
    const bothText = await both.locator('.ie-figure').innerText()
    const bothBox = await both.locator('.ie-figure').boundingBox()
    await shot(page, 'I16-both-fail')
    ev.bothFail = { text: bothText, box: bothBox }
    await both.getByRole('button', { name: '关闭弹窗' }).click()
    const orig = await openEditor(page, '标注 仅原图失败')
    await page.waitForTimeout(500)
    const origText = await orig.locator('.ie-figure').innerText()
    const origHasImg = await orig.locator('.ie-figure img').count()
    await shot(page, 'I16-original-fail')
    ev.origFail = { text: origText, hasImg: origHasImg }
    await orig.getByRole('button', { name: '关闭弹窗' }).click()

    // Delete on steak (fixture only)
    const steak = await openEditor(page, '标注 煎牛排')
    await steak.getByRole('button', { name: '删除图片' }).click()
    const confirmText = await steak.getByRole('alertdialog', { name: '确认删除' }).innerText()
    await steak.getByRole('button', { name: '确认删除' }).click()
    await expect(page.getByRole('dialog', { name: '图片标注' })).toBeHidden({ timeout: 8000 })
    ev.delete = { confirmText, deletes: store.calls.deletes.slice() }

    // I04 / I01 other dialogs: 图片详情 from search, 单张标注 from import, 批量修改, 场景管理 typography
    const nav = page.getByRole('navigation', { name: '主导航' })
    const expand = page.getByRole('button', { name: '展开导航', exact: true })
    if (await expand.isVisible()) await expand.click()
    await nav.getByRole('button', { name: /^搜索/ }).click()
    await page.waitForTimeout(400)
    const searchDialogs = await page.locator('dialog[open]').count()
    ev.searchOpenDialogs = searchDialogs
    await shot(page, 'I01-search-page')
    ev.searchPrimary = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--primary').trim())

    if (await expand.isVisible()) await expand.click()
    await nav.getByRole('button', { name: /^上传与导入/ }).click()
    await page.waitForTimeout(400)
    ev.importHasIe = await page.locator('.ie').count()
    ev.importDialogOpen = await page.locator('dialog[open]').count()
    await shot(page, 'I01-import-page')

    if (await expand.isVisible()) await expand.click()
    await nav.getByRole('button', { name: /^场景管理/ }).click()
    await page.waitForTimeout(500)
    const typoScenePage = await page.evaluate(() => {
      const pick = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null
        if (!el) return null
        const cs = getComputedStyle(el)
        return { fontSize: cs.fontSize, fontWeight: cs.fontWeight, lineHeight: cs.lineHeight, fontFamily: cs.fontFamily, color: cs.color, selector: sel, text: el.textContent?.trim().slice(0, 80) }
      }
      return {
        anyIe: !!document.querySelector('.ie'),
        h4: pick('.scene-membership-head h4, .scene-management h4, h4'),
        th: pick('.scene-table th, table th'),
        td: pick('.scene-table td, table td'),
        title: document.title,
        heading: document.querySelector('h1, h2, .page-title, .mg-page h2')?.textContent,
      }
    })
    ev.typoScenePage = typoScenePage
    ev.scenePageDialogBlockSize = await page.evaluate(() => {
      // no gallery dialog expected
      const open = document.querySelector('dialog[open]') as HTMLElement | null
      if (!open) return null
      const cs = getComputedStyle(open)
      return { blockSize: cs.blockSize, height: open.getBoundingClientRect().height, className: open.className }
    })
    await shot(page, 'I15-scene-management-page')

    // Try category config lg dialog
    if (await expand.isVisible()) await expand.click()
    const catNav = nav.getByRole('button', { name: /类别/ })
    if (await catNav.count()) {
      await catNav.first().click()
      await page.waitForTimeout(400)
      const newBtn = page.getByRole('button', { name: /新建类别|新建/ })
      if (await newBtn.count()) {
        await newBtn.first().click()
        const catDlg = page.getByRole('dialog')
        if (await catDlg.count()) {
          const geom = await catDlg.first().evaluate(el => {
            const cs = getComputedStyle(el)
            const r = (el as HTMLElement).getBoundingClientRect()
            return { title: el.querySelector('h2')?.textContent, className: (el as HTMLElement).className, width: r.width, height: r.height, blockSize: cs.blockSize, hasIe: !!el.querySelector('.ie') }
          })
          ev.categoryDialog = geom
          await shot(page, 'I04-category-dialog')
          const close = catDlg.first().getByRole('button', { name: '关闭弹窗' })
          if (await close.count()) await close.click()
        }
      }
    }

    ev.unhandled = UNHANDLED
    ev.apiUnhandled = store.calls.unhandledApi
    ev.primary = ev.searchPrimary
    writeJson('v03-isolation-and-remaining.json', ev)
  })
})
