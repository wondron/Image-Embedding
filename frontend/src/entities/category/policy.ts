/**
 * Client checks for the category form. They repeat backend/app/core/category_rules.py and
 * app/services/retrieval/category_fields.py so a mistake is shown next to its field before a
 * request is sent; the server checks everything again.
 */
import type { CategoryCreateRequestWire, CategoryUpdateRequestWire } from '../../shared/api'
import type { CategoryName, CategoryNameKind, CategoryNode } from './model'

export const CATEGORY_CODE_MAX_LEN = 128
export const CATEGORY_CODE_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$/
export const CATEGORY_NAME_MAX_LEN = 128
export const CATEGORY_LANG_MAX_LEN = 16
export const CATEGORY_LANG_PATTERN = /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/
export const CATEGORY_MAX_NAMES = 50
/** Search hits and the gallery show this language's primary name, so every category needs one. */
export const CATEGORY_DISPLAY_LANG = 'zh'
export const CATEGORY_CUISINE_MAX_LEN = 32
export const CATEGORY_CUISINE_PATTERN = /^[a-z][a-z0-9_]*$/
export const CATEGORY_MAX_CUISINES = 32
const SORT_ORDER_MIN = -2_147_483_648
const SORT_ORDER_MAX = 2_147_483_647

export type CategoryFieldName = 'parentId' | 'code' | 'zhName' | 'otherNames' | 'cuisines' | 'heatingGroupId' | 'sortOrder'
export type CategoryFieldErrors = Partial<Record<CategoryFieldName, string>>

/** What the form edits. Text fields hold what was typed; validateCategoryDraft reads them. */
export interface CategoryDraft {
  parentId: number | null
  code: string
  /** The zh primary name. */
  zhName: string
  /** Every other name: aliases, and names in other languages. */
  otherNames: CategoryName[]
  /** Cuisine codes separated by commas or spaces. */
  cuisines: string
  heatingGroupId: number | null
  sortOrder: string
  isActive: boolean
}

/** A draft that passed the checks, ready to become a request body. */
export interface CategoryPayload {
  code: string
  parentId: number | null
  /** The zh primary name first, then the other names in form order. */
  names: CategoryName[]
  cuisines: string[]
  heatingGroupId: number | null
  sortOrder: number
  isActive: boolean
}

export interface CategoryDraftContext {
  /** Codes a root may use (GET /api/categories root_codes). */
  rootCodes: readonly string[]
  /** Codes of every other node. */
  takenCodes: ReadonlySet<string>
  /** The node being edited; omit when creating. */
  original?: Pick<CategoryNode, 'id' | 'code' | 'parentId'>
  /** Ids the node must not move under: itself and its descendants. */
  forbiddenParentIds?: ReadonlySet<number>
}

export function charLength(value: string): number {
  return Array.from(value).length
}

/** A new child starts with its parent's code as a prefix, e.g. `dish.`; a root starts empty. */
export function emptyCategoryDraft(parent: Pick<CategoryNode, 'id' | 'code'> | null, code = ''): CategoryDraft {
  return {
    parentId: parent?.id ?? null,
    code: parent ? `${parent.code}.` : code,
    zhName: '',
    otherNames: [],
    cuisines: '',
    heatingGroupId: null,
    sortOrder: '0',
    isActive: true,
  }
}

function isDisplayName(name: CategoryName): boolean {
  return name.lang === CATEGORY_DISPLAY_LANG && name.kind === 'primary'
}

export function categoryDraftFromNode(node: CategoryNode): CategoryDraft {
  const display = node.names.find(isDisplayName)
  return {
    parentId: node.parentId,
    code: node.code,
    zhName: display?.name ?? '',
    otherNames: node.names.filter(name => name !== display).map(name => ({ ...name })),
    cuisines: node.cuisines.join(', '),
    heatingGroupId: node.heatingGroup?.id ?? null,
    sortOrder: String(node.sortOrder),
    isActive: node.isActive,
  }
}

/** Split what was typed (commas, 、, semicolons or blanks) into codes, without duplicates. */
export function parseCuisines(text: string): string[] {
  const cuisines: string[] = []
  for (const part of text.split(/[\s,，、;；]+/)) {
    const cuisine = part.trim()
    if (cuisine && !cuisines.includes(cuisine)) cuisines.push(cuisine)
  }
  return cuisines
}

function codeError(code: string, draft: CategoryDraft, context: CategoryDraftContext): string | null {
  if (!code) return '请填写编码。'
  if (charLength(code) > CATEGORY_CODE_MAX_LEN) return `编码最长 ${CATEGORY_CODE_MAX_LEN} 个字符。`
  if (!CATEGORY_CODE_PATTERN.test(code)) {
    return '编码只能用小写字母、数字和下划线，用点分段，并以字母开头，例如 dish.pizza。'
  }
  if (context.takenCodes.has(code)) return '该编码已被其他类别使用。'
  // Like the server: checked only when the code or the position changes, so an older root can still be edited.
  const original = context.original
  const changed = !original || original.code !== code || original.parentId !== draft.parentId
  if (draft.parentId === null && changed && !context.rootCodes.includes(code)) {
    return `根类别的编码只能是：${context.rootCodes.join('、')}。其他类别需要选择上级。`
  }
  return null
}

function otherNamesError(zhName: string, names: readonly CategoryName[]): string | null {
  if (names.length + 1 > CATEGORY_MAX_NAMES) return `名称最多 ${CATEGORY_MAX_NAMES} 个。`
  const seen = new Set<string>([`${CATEGORY_DISPLAY_LANG}\n${zhName}`])
  const primaries = new Set<string>([CATEGORY_DISPLAY_LANG])
  for (const [index, entry] of names.entries()) {
    const row = `第 ${index + 1} 行：`
    if (!entry.lang) return `${row}请填写语言代码。`
    if (charLength(entry.lang) > CATEGORY_LANG_MAX_LEN || !CATEGORY_LANG_PATTERN.test(entry.lang)) {
      return `${row}语言代码需为小写，例如 zh、en。`
    }
    if (!entry.name) return `${row}请填写名称。`
    if (charLength(entry.name) > CATEGORY_NAME_MAX_LEN) return `${row}名称最长 ${CATEGORY_NAME_MAX_LEN} 个字符。`
    const key = `${entry.lang}\n${entry.name}`
    if (seen.has(key)) return `${row}「${entry.name}」重复了。`
    seen.add(key)
    if (entry.kind === 'primary') {
      if (primaries.has(entry.lang)) {
        return entry.lang === CATEGORY_DISPLAY_LANG
          ? `${row}中文主名已在上面填写，这里只能填中文别名。`
          : `${row}${entry.lang} 已经有主名，每种语言只能有一个。`
      }
      primaries.add(entry.lang)
    }
  }
  return null
}

function cuisinesError(cuisines: readonly string[]): string | null {
  if (cuisines.length > CATEGORY_MAX_CUISINES) return `菜系最多 ${CATEGORY_MAX_CUISINES} 个。`
  const bad = cuisines.find(cuisine => (
    charLength(cuisine) > CATEGORY_CUISINE_MAX_LEN || !CATEGORY_CUISINE_PATTERN.test(cuisine)
  ))
  return bad ? `菜系「${bad}」需以小写字母开头，只含小写字母、数字或下划线，最长 ${CATEGORY_CUISINE_MAX_LEN} 个字符。` : null
}

function parseSortOrder(text: string): number | null {
  const trimmed = text.trim()
  if (!/^-?\d+$/.test(trimmed)) return null
  const value = Number(trimmed)
  return value >= SORT_ORDER_MIN && value <= SORT_ORDER_MAX ? value : null
}

export function validateCategoryDraft(draft: CategoryDraft, context: CategoryDraftContext): {
  ok: true
  payload: CategoryPayload
} | {
  ok: false
  fieldErrors: CategoryFieldErrors
} {
  const fieldErrors: CategoryFieldErrors = {}
  const code = draft.code.trim()
  const zhName = draft.zhName.trim()
  const otherNames = draft.otherNames.map(entry => ({
    lang: entry.lang.trim(),
    name: entry.name.trim(),
    kind: entry.kind,
  }))
  const cuisines = parseCuisines(draft.cuisines)
  const sortOrder = parseSortOrder(draft.sortOrder)

  if (draft.parentId !== null && context.forbiddenParentIds?.has(draft.parentId)) {
    fieldErrors.parentId = '不能移动到自己或自己的下级类别下面。'
  }
  const badCode = codeError(code, draft, context)
  if (badCode) fieldErrors.code = badCode
  if (!zhName) fieldErrors.zhName = '请填写中文名称。'
  else if (charLength(zhName) > CATEGORY_NAME_MAX_LEN) fieldErrors.zhName = `名称最长 ${CATEGORY_NAME_MAX_LEN} 个字符。`
  const badNames = otherNamesError(zhName, otherNames)
  if (badNames) fieldErrors.otherNames = badNames
  const badCuisines = cuisinesError(cuisines)
  if (badCuisines) fieldErrors.cuisines = badCuisines
  if (sortOrder === null) fieldErrors.sortOrder = '同级排序需为整数。'

  if (Object.keys(fieldErrors).length > 0 || sortOrder === null) return { ok: false, fieldErrors }
  return {
    ok: true,
    payload: {
      code,
      parentId: draft.parentId,
      names: [{ lang: CATEGORY_DISPLAY_LANG, name: zhName, kind: 'primary' }, ...otherNames],
      cuisines,
      heatingGroupId: draft.heatingGroupId,
      sortOrder,
      isActive: draft.isActive,
    },
  }
}

export function toCategoryCreateBody(payload: CategoryPayload): CategoryCreateRequestWire {
  return {
    code: payload.code,
    parent_id: payload.parentId,
    names: payload.names.map(name => ({ ...name })),
    cuisines: [...payload.cuisines],
    heating_group_id: payload.heatingGroupId,
    sort_order: payload.sortOrder,
    is_active: payload.isActive,
  }
}

function nameKeys(names: readonly { lang: string; name: string; kind: CategoryNameKind }[]): string {
  return names.map(name => `${name.lang}\n${name.name}\n${name.kind}`).sort().join('\n\n')
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

/**
 * The PATCH body: only what differs from the node as loaded, so an edit does not write back
 * fields somebody else changed meanwhile. null when nothing differs. Names are a set (their
 * order is not stored); cuisines keep their order.
 */
export function toCategoryUpdateBody(node: CategoryNode, payload: CategoryPayload): CategoryUpdateRequestWire | null {
  const body: CategoryUpdateRequestWire = {}
  if (payload.code !== node.code) body.code = payload.code
  if (payload.parentId !== node.parentId) body.parent_id = payload.parentId
  if (nameKeys(payload.names) !== nameKeys(node.names)) body.names = payload.names.map(name => ({ ...name }))
  if (!sameList(payload.cuisines, node.cuisines)) body.cuisines = [...payload.cuisines]
  if (payload.heatingGroupId !== (node.heatingGroup?.id ?? null)) body.heating_group_id = payload.heatingGroupId
  if (payload.sortOrder !== node.sortOrder) body.sort_order = payload.sortOrder
  if (payload.isActive !== node.isActive) body.is_active = payload.isActive
  return Object.keys(body).length > 0 ? body : null
}
