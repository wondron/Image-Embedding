import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../shared/api'
import { createCategory, deleteCategory, listCategories, updateCategory } from './api'
import { toCategoryFailure } from './errors'
import {
  buildCategoryTree,
  categoryAncestors,
  categoryLabel,
  effectiveCuisines,
  effectiveHeatingGroup,
  flattenCategoryTree,
  subtreeIds,
  toCategoryDetail,
  toCategoryList,
  toCategoryNode,
  toCategorySceneSync,
  toDeletedCategoryId,
  type CategoryNode,
} from './model'
import {
  categoryDraftFromNode,
  emptyCategoryDraft,
  parseCuisines,
  toCategoryCreateBody,
  toCategoryUpdateBody,
  validateCategoryDraft,
  type CategoryDraft,
  type CategoryDraftContext,
} from './policy'

const json = (body: unknown, status = 200, trace = 'trace-h') => new Response(JSON.stringify(body), {
  status,
  headers: { 'X-Trace-Id': trace },
})
const zh = (name: string) => ({ lang: 'zh', name, kind: 'primary' as const })
const grill = { id: 2, code: 'grill', name: '烤' }
/** A node as the API sends it (schemas/categories.py CategoryItem). */
const wire = (id: number, code: string, parent_id: number | null, extra: Record<string, unknown> = {}) => ({
  id,
  code,
  parent_id,
  path: String(id),
  name: `名称${id}`,
  names: [zh(`名称${id}`)],
  cuisines: [],
  heating_group: null,
  sort_order: 0,
  is_active: true,
  image_count: 0,
  scene_rule_count: 0,
  created_at: '2026-11-10T08:00:00+00:00',
  updated_at: '2026-11-10T08:00:00+00:00',
  ...extra,
})
const node = (id: number, code: string, parentId: number | null, extra: Record<string, unknown> = {}): CategoryNode => {
  const parsed = toCategoryNode(wire(id, code, parentId, extra))
  if (!parsed) throw new Error('fixture is not a valid node')
  return parsed
}
/** dish(1) > pizza(2) > slice(4); dish > noodle(3); ingredient(5) */
const nodes = [
  node(1, 'dish', null, { cuisines: ['chinese'], heating_group: grill }),
  node(5, 'ingredient', null),
  node(2, 'dish.pizza', 1, { cuisines: ['italian'] }),
  node(3, 'dish.noodle', 1),
  node(4, 'dish.pizza.slice', 2),
]
const ROOT_CODES = ['ingredient', 'dish', 'dessert', 'beverage', 'mixed', 'other']
const context = (extra: Partial<CategoryDraftContext> = {}): CategoryDraftContext => ({
  rootCodes: ROOT_CODES,
  rootCount: 2,
  takenCodes: new Set(['dish', 'dish.pizza']),
  ...extra,
})
const draft = (extra: Partial<CategoryDraft> = {}): CategoryDraft => ({
  ...emptyCategoryDraft({ id: 1, code: 'dish' }),
  code: 'dish.rice',
  zhName: '米饭',
  ...extra,
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('category parsers', () => {
  it('reads a node field by field', () => {
    expect(toCategoryNode(wire(2, 'dish.pizza', 1, {
      path: '1.2',
      name: '披萨',
      names: [zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
      cuisines: ['italian'],
      heating_group: grill,
      sort_order: 3,
      is_active: false,
      image_count: 7,
      scene_rule_count: 1,
    }))).toEqual({
      id: 2,
      code: 'dish.pizza',
      parentId: 1,
      path: '1.2',
      name: '披萨',
      names: [zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
      cuisines: ['italian'],
      heatingGroup: grill,
      sortOrder: 3,
      isActive: false,
      imageCount: 7,
      sceneRuleCount: 1,
      createdAt: '2026-11-10T08:00:00+00:00',
      updatedAt: '2026-11-10T08:00:00+00:00',
    })
  })

  it('drops what it cannot read instead of guessing', () => {
    expect(toCategoryNode(wire(0, 'dish', null))).toBeNull()
    expect(toCategoryNode(wire(1, '  ', null))).toBeNull()
    expect(toCategoryNode('dish')).toBeNull()
    const loose = toCategoryNode({
      id: 3, code: 'dish.x', parent_id: 'one', name: 5,
      names: [zh('好'), { lang: 'zh', name: '坏', kind: 'main' }, 'x'],
      heating_group: { id: 'two' }, image_count: -4, is_active: 'no',
    })
    expect(loose).toMatchObject({
      parentId: null, name: null, names: [zh('好')], cuisines: [], heatingGroup: null,
      sortOrder: 0, imageCount: 0, sceneRuleCount: 0, path: '3',
    })
    // only an explicit false disables a node
    expect(loose?.isActive).toBe(true)
    expect(categoryLabel(loose!)).toBe('dish.x')
    expect(categoryLabel(nodes[0])).toBe('名称1')
  })

  it('reads the list with its heating groups and root codes, and the write bodies', () => {
    const body = { ok: true, categories: [wire(1, 'dish', null), { id: 'bad' }], heating_groups: [grill, {}], root_codes: ROOT_CODES }
    expect(toCategoryList(body, 'trace-l')).toMatchObject({
      categories: [{ id: 1, code: 'dish' }], heatingGroups: [grill], rootCodes: ROOT_CODES, traceId: 'trace-l',
    })
    expect(toCategoryList({ ok: true }, null)).toBeNull()
    expect(toCategoryList({ ok: false, categories: [] }, null)).toBeNull()
    expect(toCategoryDetail({ ok: true, category: wire(1, 'dish', null) })).toMatchObject({ id: 1 })
    expect(toCategoryDetail({ ok: true })).toBeNull()
    expect(toDeletedCategoryId({ ok: true, deleted: { id: 4, code: 'x' } })).toBe(4)
    expect(toDeletedCategoryId({ ok: true })).toBeNull()
  })

  it('reads what a move did to the images below the node', () => {
    const body = (sceneSync: unknown) => ({ ok: true, category: wire(2, 'dish.pizza', 5), scene_sync: sceneSync })
    expect(toCategorySceneSync(body({ images: 9, changed: 4, pushed: 3, pending: 1, vector_sync: 'partial', sync_required: true })))
      .toEqual({ images: 9, changed: 4, pushed: 3, pending: 1, vectorSync: 'partial' })
    // Nothing could change (any other patch), or no body: nothing to say
    expect(toCategorySceneSync(body(null))).toBeNull()
    expect(toCategorySceneSync({ ok: true, category: wire(2, 'dish.pizza', 5) })).toBeNull()
    expect(toCategorySceneSync({ ok: false, scene_sync: { changed: 1 } })).toBeNull()
    // Counts never contradict each other; an unknown status falls back on them
    expect(toCategorySceneSync(body({ images: 1, changed: 2, pushed: 5, pending: 7, vector_sync: 'later' })))
      .toEqual({ images: 2, changed: 2, pushed: 2, pending: 2, vectorSync: 'partial' })
    expect(toCategorySceneSync(body({ changed: 'x' }))).toEqual({ images: 0, changed: 0, pushed: 0, pending: 0, vectorSync: 'pushed' })
  })
})

describe('category tree', () => {
  const tree = buildCategoryTree(nodes)

  it('nests by parent and keeps the list order among siblings', () => {
    expect(tree.roots.map(entry => entry.node.code)).toEqual(['dish', 'ingredient'])
    expect(tree.byId.get(1)!.children.map(entry => entry.node.code)).toEqual(['dish.pizza', 'dish.noodle'])
    expect(flattenCategoryTree(tree).map(entry => [entry.node.code, entry.depth])).toEqual([
      ['dish', 0], ['dish.pizza', 1], ['dish.pizza.slice', 2], ['dish.noodle', 1], ['ingredient', 0],
    ])
  })

  it('finds a subtree and the ancestors of a node', () => {
    expect([...subtreeIds(tree, 2)].sort()).toEqual([2, 4])
    expect([...subtreeIds(tree, 1)].sort()).toEqual([1, 2, 3, 4])
    expect(subtreeIds(tree, 99).size).toBe(0)
    expect(categoryAncestors(tree, 4).map(ancestor => ancestor.code)).toEqual(['dish', 'dish.pizza'])
    expect(categoryAncestors(tree, 1)).toEqual([])
  })

  it('resolves cuisines and the heating group from the nearest ancestor that has them', () => {
    expect(effectiveCuisines(tree, 2)).toEqual({ value: ['italian'], from: null })
    expect(effectiveCuisines(tree, 4)).toMatchObject({ value: ['italian'], from: { code: 'dish.pizza' } })
    expect(effectiveCuisines(tree, 3)).toMatchObject({ value: ['chinese'], from: { code: 'dish' } })
    expect(effectiveCuisines(tree, 5)).toEqual({ value: [], from: null })
    expect(effectiveHeatingGroup(tree, 1)).toEqual({ value: grill, from: null })
    expect(effectiveHeatingGroup(tree, 4)).toMatchObject({ value: grill, from: { code: 'dish' } })
    expect(effectiveHeatingGroup(tree, 5)).toEqual({ value: null, from: null })
  })

  it('never hides a node: orphans and parent cycles are shown at the top level', () => {
    const broken = buildCategoryTree([node(1, 'a', 2), node(2, 'b', 1), node(3, 'c', 99), node(4, 'd', 4)])
    const flat = flattenCategoryTree(broken)
    expect(flat.map(entry => entry.node.code).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(new Set(flat.map(entry => entry.node.id)).size).toBe(4) // each node once: rendering ends
    expect(broken.roots.map(entry => entry.node.code)).toEqual(['c', 'd', 'a'])
    expect(categoryAncestors(broken, 1).map(ancestor => ancestor.code)).toEqual(['b'])
  })
})

describe('category draft', () => {
  it('starts a child with its parent code as a prefix and a root with the chosen domain', () => {
    expect(emptyCategoryDraft({ id: 1, code: 'dish' })).toMatchObject({ parentId: 1, code: 'dish.', sortOrder: '0', isActive: true })
    expect(emptyCategoryDraft(null, 'dessert')).toMatchObject({ parentId: null, code: 'dessert' })
  })

  it('splits a node into the zh primary name and the other names', () => {
    const edited = node(2, 'dish.pizza', 1, {
      names: [{ lang: 'zh', name: '比萨', kind: 'alias' }, zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
      cuisines: ['italian', 'american'], heating_group: grill, sort_order: 4, is_active: false,
    })
    expect(categoryDraftFromNode(edited)).toEqual({
      parentId: 1, code: 'dish.pizza', zhName: '披萨',
      otherNames: [{ lang: 'zh', name: '比萨', kind: 'alias' }, { lang: 'en', name: 'Pizza', kind: 'primary' }],
      cuisines: 'italian, american', heatingGroupId: 2, sortOrder: '4', isActive: false,
    })
    expect(categoryDraftFromNode(node(9, 'legacy', null, { names: [] })).zhName).toBe('')
  })

  it('parses cuisines typed with commas, 、 or blanks', () => {
    expect(parseCuisines(' chinese, american，japanese、 chinese\nthai;korean ')).toEqual(['chinese', 'american', 'japanese', 'thai', 'korean'])
    expect(parseCuisines('  ')).toEqual([])
  })

  it('turns a valid draft into a payload with the zh primary name first', () => {
    const result = validateCategoryDraft(draft({
      code: ' dish.rice ', zhName: ' 米饭 ',
      otherNames: [{ lang: ' en ', name: ' Rice ', kind: 'primary' }, { lang: 'zh', name: '白饭', kind: 'alias' }],
      cuisines: 'chinese, japanese', heatingGroupId: 2, sortOrder: ' -3 ', isActive: false,
    }), context())
    expect(result).toEqual({
      ok: true,
      payload: {
        code: 'dish.rice', parentId: 1,
        names: [zh('米饭'), { lang: 'en', name: 'Rice', kind: 'primary' }, { lang: 'zh', name: '白饭', kind: 'alias' }],
        cuisines: ['chinese', 'japanese'], heatingGroupId: 2, sortOrder: -3, isActive: false,
      },
    })
    if (result.ok) {
      expect(toCategoryCreateBody(result.payload)).toEqual({
        code: 'dish.rice', parent_id: 1,
        names: [zh('米饭'), { lang: 'en', name: 'Rice', kind: 'primary' }, { lang: 'zh', name: '白饭', kind: 'alias' }],
        cuisines: ['chinese', 'japanese'], heating_group_id: 2, sort_order: -3, is_active: false,
      })
    }
  })

  it('reports each bad field', () => {
    const errorsOf = (input: Partial<CategoryDraft>, ctx = context()) => {
      const result = validateCategoryDraft(draft(input), ctx)
      return result.ok ? {} : result.fieldErrors
    }
    for (const code of ['', 'dish..rice', 'dish.', '米饭', '1dish', 'dish rice', 'x'.repeat(129)]) {
      expect(errorsOf({ code }).code, code).toBeTruthy()
    }
    expect(errorsOf({ code: 'dish.pizza' }).code).toContain('已被其他类别使用')
    expect(errorsOf({ zhName: '  ' }).zhName).toBeTruthy()
    expect(errorsOf({ zhName: '长'.repeat(129) }).zhName).toBeTruthy()
    expect(errorsOf({ cuisines: 'Chinese' }).cuisines).toContain('Chinese')
    expect(errorsOf({ cuisines: Array.from({ length: 33 }, (_, i) => `c${i}`).join(',') }).cuisines).toBeTruthy()
    for (const sortOrder of ['', 'abc', '1.5', '2147483648']) expect(errorsOf({ sortOrder }).sortOrder, sortOrder).toBeTruthy()
    expect(errorsOf({ parentId: 2 }, context({ forbiddenParentIds: new Set([2, 4]) })).parentId).toBeTruthy()
    expect(errorsOf({ code: 'Dish', zhName: '', sortOrder: 'x' })).toMatchObject({ code: expect.any(String), zhName: expect.any(String), sortOrder: expect.any(String) })
  })

  it('preserves mixed-case codes and rejects duplicates regardless of casing', () => {
    const result = validateCategoryDraft(draft({ code: ' Ingredient.HongSu ' }), context())
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.payload.code).toBe('Ingredient.HongSu')
      expect(toCategoryCreateBody(result.payload).code).toBe('Ingredient.HongSu')
    }
    for (const [code, taken] of [['DISH.Pizza', 'dish.pizza'], ['dish.PIZZA', 'Dish.Pizza']]) {
      const duplicate = validateCategoryDraft(draft({ code }), context({ takenCodes: new Set([taken]) }))
      expect(duplicate.ok).toBe(false)
      if (!duplicate.ok) expect(duplicate.fieldErrors.code).toContain('已被其他类别使用')
    }
  })

  it('allows case-only renames when the current node is excluded from taken codes', () => {
    const loaded = node(2, 'dish.pizza', 1)
    const result = validateCategoryDraft({ ...categoryDraftFromNode(loaded), code: 'Dish.Pizza' },
      context({ takenCodes: new Set(['dish']), original: loaded }))
    expect(result.ok).toBe(true)
    if (result.ok) expect(toCategoryUpdateBody(loaded, result.payload)).toEqual({ code: 'Dish.Pizza' })
  })

  it('checks the other names like the server does', () => {
    const errorOf = (otherNames: CategoryDraft['otherNames']) => {
      const result = validateCategoryDraft(draft({ otherNames }), context())
      return result.ok ? undefined : result.fieldErrors.otherNames
    }
    expect(errorOf([{ lang: 'zh', name: '白饭', kind: 'alias' }, { lang: 'en', name: 'Rice', kind: 'primary' }])).toBeUndefined()
    expect(errorOf([{ lang: '', name: 'Rice', kind: 'alias' }])).toContain('第 1 行')
    expect(errorOf([{ lang: 'EN', name: 'Rice', kind: 'alias' }])).toContain('小写')
    expect(errorOf([{ lang: 'en', name: ' ', kind: 'alias' }])).toContain('请填写名称')
    expect(errorOf([{ lang: 'zh', name: '米饭', kind: 'alias' }])).toContain('重复')
    expect(errorOf([{ lang: 'zh', name: '白饭', kind: 'primary' }])).toContain('中文主名已在上面填写')
    expect(errorOf([{ lang: 'en', name: 'Rice', kind: 'primary' }, { lang: 'en', name: 'Steamed rice', kind: 'primary' }])).toContain('第 2 行')
    expect(errorOf(Array.from({ length: 50 }, (_, i) => ({ lang: 'zh', name: `别名${i}`, kind: 'alias' as const })))).toContain('最多')
  })

  it('lets only a domain code be a root, and only checks that when the code or the place changes', () => {
    const rootDraft = (code: string) => draft({ parentId: null, code })
    expect(validateCategoryDraft(rootDraft('dessert'), context()).ok).toBe(true)
    expect(validateCategoryDraft(rootDraft('mixed'), context()).ok).toBe(true)
    expect(validateCategoryDraft(rootDraft('other'), context()).ok).toBe(true)
    expect(validateCategoryDraft(rootDraft('OtHeR'), context()).ok).toBe(true)
    const snack = validateCategoryDraft(rootDraft('snack'), context())
    expect(snack.ok).toBe(false)
    if (!snack.ok) expect(snack.fieldErrors.code).toContain('ingredient、dish、dessert、beverage、mixed、other')
    // a root stored before the rule keeps its code while it is not renamed or moved
    const legacy = { id: 9, code: 'legacy', parentId: null }
    expect(validateCategoryDraft(rootDraft('legacy'), context({ original: legacy })).ok).toBe(true)
    expect(validateCategoryDraft(rootDraft('legacy2'), context({ original: legacy })).ok).toBe(false)
    expect(validateCategoryDraft(rootDraft('dish.rice'), context({ original: { id: 7, code: 'dish.rice', parentId: 1 } })).ok).toBe(false)
  })

  it('limits roots to six, including legacy roots, while allowing edits and children', () => {
    const root = draft({ parentId: null, code: 'other', zhName: '其他' })
    expect(validateCategoryDraft(root, context({ rootCount: 5 })).ok).toBe(true)
    const seventh = validateCategoryDraft(root, context({ rootCount: 6 }))
    expect(seventh.ok).toBe(false)
    if (!seventh.ok) expect(seventh.fieldErrors.parentId).toContain('最多只能有 6 个')
    expect(validateCategoryDraft(root, context({ rootCount: 6, original: { id: 9, code: 'other', parentId: null } })).ok).toBe(true)
    expect(validateCategoryDraft(root, context({ rootCount: 6, original: { id: 9, code: 'other', parentId: 1 } })).ok).toBe(false)
    expect(validateCategoryDraft(draft(), context({ rootCount: 6 })).ok).toBe(true)
  })

  it('patches only what differs from the node as loaded', () => {
    const loaded = node(2, 'dish.pizza', 1, {
      names: [zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }], cuisines: ['italian', 'american'],
      heating_group: grill, sort_order: 4,
    })
    const payloadOf = (input: Partial<CategoryDraft> = {}) => {
      const result = validateCategoryDraft({ ...categoryDraftFromNode(loaded), ...input }, context({ takenCodes: new Set(['dish']) }))
      if (!result.ok) throw new Error(JSON.stringify(result.fieldErrors))
      return result.payload
    }
    expect(toCategoryUpdateBody(loaded, payloadOf())).toBeNull()
    expect(toCategoryUpdateBody(loaded, payloadOf({ sortOrder: '5' }))).toEqual({ sort_order: 5 })
    expect(toCategoryUpdateBody(loaded, payloadOf({ parentId: 5, isActive: false }))).toEqual({ parent_id: 5, is_active: false })
    expect(toCategoryUpdateBody(loaded, payloadOf({ parentId: null, code: 'dessert' }))).toEqual({ code: 'dessert', parent_id: null })
    expect(toCategoryUpdateBody(loaded, payloadOf({ heatingGroupId: null, cuisines: 'american, italian' }))).toEqual({
      heating_group_id: null, cuisines: ['american', 'italian'],
    })
    expect(toCategoryUpdateBody(loaded, payloadOf({ zhName: '意式披萨' }))).toEqual({
      names: [zh('意式披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
    })
    // the same names in another order are not a change
    const reordered = { ...loaded, names: [...loaded.names].reverse() }
    expect(toCategoryUpdateBody(reordered, payloadOf())).toBeNull()
  })
})

describe('toCategoryFailure', () => {
  const failure = (code: string | null, status: number | null, extra: Partial<ConstructorParameters<typeof ApiError>[1]> = {}) => (
    toCategoryFailure(new ApiError('失败', { code, status, detail: 'detail', traceId: 'trace-e', ...extra }))
  )

  it('maps the category codes', () => {
    expect(failure('CATEGORY_NOT_FOUND', 404)).toMatchObject({ kind: 'not_found', code: 'CATEGORY_NOT_FOUND', traceId: 'trace-e' })
    expect(failure('CATEGORY_CODE_EXISTS', 409)).toMatchObject({ kind: 'code_exists', fieldErrors: { code: expect.any(String) } })
    expect(failure('CATEGORY_IN_USE', 409)).toMatchObject({ kind: 'in_use', detail: 'detail' })
    expect(failure('CATEGORY_POSTGRES_FAILED', 502)).toMatchObject({ kind: 'unknown', status: 502 })
    expect(failure('NOT_READY', 503)).toMatchObject({ kind: 'unknown' })
  })

  it('puts validation errors on the form field they belong to', () => {
    const errors = [
      { loc: ['body', 'code'], msg: 'String should match pattern' },
      { loc: ['body', 'names', 0, 'name'], msg: 'too long' },
      { loc: ['body', 'names', 2, 'lang'], msg: 'bad lang' },
      { loc: ['body', 'cuisines', 1], msg: 'bad cuisine' },
      { loc: ['body', 'sort_order'], msg: 'not an int' },
      { loc: ['body', 'parent_id'], msg: 'gt 0' },
      { loc: ['body', 'heating_group_id'], msg: 'gt 0' },
      { loc: ['body', 'path'], msg: 'extra' },
    ]
    expect(failure('VALIDATION_ERROR', 422, { errors }).fieldErrors).toEqual({
      code: 'String should match pattern', zhName: 'too long', otherNames: 'bad lang', cuisines: 'bad cuisine',
      sortOrder: 'not an int', parentId: 'gt 0', heatingGroupId: 'gt 0',
    })
    // a rule the schema cannot say arrives without a field list
    expect(failure('VALIDATION_ERROR', 422)).toMatchObject({ kind: 'validation', fieldErrors: {}, detail: 'detail' })
  })

  it('calls an answer without a clear body uncertain', () => {
    expect(failure(null, null).kind).toBe('uncertain')
    expect(failure(null, 504).kind).toBe('uncertain')
    expect(failure(null, 502).kind).toBe('uncertain')
    expect(toCategoryFailure(new DOMException('请求超时', 'TimeoutError')).kind).toBe('uncertain')
    expect(toCategoryFailure(new DOMException('Aborted', 'AbortError')).kind).toBe('uncertain')
    expect(toCategoryFailure(new Error('boom'))).toMatchObject({ kind: 'unknown', detail: 'boom' })
  })
})

describe('category api', () => {
  const listBody = { ok: true, categories: [wire(1, 'dish', null)], heating_groups: [grill], root_codes: ROOT_CODES }

  it('loads the tree and takes the trace id from the header', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(listBody))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listCategories({ baseUrl: 'http://127.0.0.1:8320/' })).resolves.toMatchObject({
      categories: [{ code: 'dish' }], heatingGroups: [grill], traceId: 'trace-h',
    })
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8320/api/categories', { method: 'GET', signal: undefined })
  })

  it('rejects a list body that is not the contract', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ok: true, items: [] })))
    await expect(listCategories()).rejects.toMatchObject({ name: 'ApiError', status: 200, traceId: 'trace-h' })
  })

  it('creates, patches and deletes with the contract methods and bodies', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, category: wire(2, 'dish.pizza', 1) }, 201, 'trace-c'))
      .mockResolvedValueOnce(json({ ok: true, category: wire(2, 'dish.pizza', 5) }, 200, 'trace-u'))
      .mockResolvedValueOnce(json({ ok: true, deleted: { id: 2, code: 'dish.pizza' } }, 200, 'trace-d'))
    vi.stubGlobal('fetch', fetchMock)
    const createBody = {
      code: 'dish.pizza', parent_id: 1, names: [zh('披萨')], cuisines: [], heating_group_id: null, sort_order: 0, is_active: true,
    }
    await expect(createCategory(createBody)).resolves.toMatchObject({ node: { id: 2, parentId: 1 }, traceId: 'trace-c' })
    await expect(updateCategory(2, { parent_id: 5 })).resolves.toMatchObject({ node: { parentId: 5 }, sceneSync: null, traceId: 'trace-u' })
    await expect(deleteCategory(2)).resolves.toEqual({ deletedId: 2, traceId: 'trace-d' })

    const calls = fetchMock.mock.calls.map(([url, init]) => [init.method, url, init.body ? JSON.parse(init.body) : undefined])
    expect(calls).toEqual([
      ['POST', '/api/categories', createBody],
      ['PATCH', '/api/categories/2', { parent_id: 5 }],
      ['DELETE', '/api/categories/2', undefined],
    ])
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('treats a 2xx it cannot read as a write that happened', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not-json', { status: 201, headers: { 'X-Trace-Id': 'trace-x' } })))
    await expect(createCategory({
      code: 'dish', parent_id: null, names: [zh('菜品')], cuisines: [], heating_group_id: null, sort_order: 0, is_active: true,
    })).resolves.toEqual({ node: null, sceneSync: null, traceId: 'trace-x' })
    await expect(updateCategory(1, { sort_order: 1 })).resolves.toEqual({ node: null, sceneSync: null, traceId: 'trace-x' })
    await expect(deleteCategory(1)).resolves.toEqual({ deletedId: null, traceId: 'trace-x' })
  })

  it('does not turn a refusal into a write', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({
      ok: false, error: 'CATEGORY_IN_USE', detail: "category 'dish' is still in use (images: 3)", trace_id: 'trace-e',
    }, 409)))
    await expect(deleteCategory(1)).rejects.toMatchObject({ code: 'CATEGORY_IN_USE', status: 409, traceId: 'trace-e' })
    await expect(updateCategory(1, { code: 'dish' })).rejects.toMatchObject({ status: 409 })
  })
})
