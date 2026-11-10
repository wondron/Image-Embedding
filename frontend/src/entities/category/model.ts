import { asBoolean, asInteger, asText, asTextList, untrusted } from '../../shared/lib/parse'
import type {
  CategoryDeleteResponseWire,
  CategoryItemWire,
  CategoryListResponseWire,
  CategoryNameWire,
  CategoryResponseWire,
  HeatingGroupWire,
} from '../../shared/api'

export type CategoryNameKind = 'primary' | 'alias'

/** One name of a category (a row of category_names). */
export interface CategoryName {
  lang: string
  name: string
  kind: CategoryNameKind
}

export interface HeatingGroupRef {
  id: number
  code: string
  name: string
}

/** One node of the category tree, as GET /api/categories returns it. */
export interface CategoryNode {
  id: number
  code: string
  parentId: number | null
  path: string
  /** The zh primary name; null when the node has none (then the code is shown). */
  name: string | null
  names: CategoryName[]
  /** Own cuisines; empty means the nearest ancestor's apply. */
  cuisines: string[]
  /** Own heating group; null means the nearest ancestor's applies. */
  heatingGroup: HeatingGroupRef | null
  sortOrder: number
  isActive: boolean
  imageCount: number
  sceneRuleCount: number
  createdAt: string | null
  updatedAt: string | null
}

export interface CategoryListResult {
  /** Parents before children, siblings in display order. */
  categories: CategoryNode[]
  heatingGroups: HeatingGroupRef[]
  /** The codes a root may use. */
  rootCodes: string[]
  traceId: string | null
}

function asCount(value: unknown): number {
  const count = asInteger(value)
  return count !== null && count > 0 ? count : 0
}

function positiveId(value: unknown): number | null {
  const id = asInteger(value)
  return id !== null && id > 0 ? id : null
}

export function toCategoryName(value: unknown): CategoryName | null {
  const body = untrusted<CategoryNameWire>(value)
  const lang = asText(body?.lang)
  const name = asText(body?.name)
  if (!body || !lang || !name) return null
  if (body.kind !== 'primary' && body.kind !== 'alias') return null
  return { lang, name, kind: body.kind }
}

export function toHeatingGroupRef(value: unknown): HeatingGroupRef | null {
  const body = untrusted<HeatingGroupWire>(value)
  const id = positiveId(body?.id)
  const code = asText(body?.code)
  if (!body || id === null || !code) return null
  return { id, code, name: asText(body.name) ?? code }
}

export function toCategoryNode(value: unknown): CategoryNode | null {
  const body = untrusted<CategoryItemWire>(value)
  const id = positiveId(body?.id)
  const code = asText(body?.code)
  if (!body || id === null || !code) return null
  const names = Array.isArray(body.names)
    ? body.names.flatMap(entry => {
      const name = toCategoryName(entry)
      return name ? [name] : []
    })
    : []
  return {
    id,
    code,
    parentId: positiveId(body.parent_id),
    path: asText(body.path) ?? String(id),
    name: asText(body.name),
    names,
    cuisines: asTextList(body.cuisines),
    heatingGroup: toHeatingGroupRef(body.heating_group),
    sortOrder: asInteger(body.sort_order) ?? 0,
    // Only an explicit false is "disabled": a missing flag must not grey a node out.
    isActive: asBoolean(body.is_active) !== false,
    imageCount: asCount(body.image_count),
    sceneRuleCount: asCount(body.scene_rule_count),
    createdAt: asText(body.created_at),
    updatedAt: asText(body.updated_at),
  }
}

export function toCategoryList(value: unknown, traceId: string | null): CategoryListResult | null {
  const body = untrusted<CategoryListResponseWire>(value)
  if (!body || body.ok !== true || !Array.isArray(body.categories)) return null
  return {
    categories: body.categories.flatMap(entry => {
      const node = toCategoryNode(entry)
      return node ? [node] : []
    }),
    heatingGroups: Array.isArray(body.heating_groups)
      ? body.heating_groups.flatMap(entry => {
        const group = toHeatingGroupRef(entry)
        return group ? [group] : []
      })
      : [],
    rootCodes: asTextList(body.root_codes),
    traceId,
  }
}

/** The node of a POST / PATCH body; null when the body is not the contract's shape. */
export function toCategoryDetail(value: unknown): CategoryNode | null {
  const body = untrusted<CategoryResponseWire>(value)
  if (!body || body.ok !== true) return null
  return toCategoryNode(body.category)
}

/** The id of a DELETE body; null when the body is not the contract's shape. */
export function toDeletedCategoryId(value: unknown): number | null {
  const body = untrusted<CategoryDeleteResponseWire>(value)
  if (!body || body.ok !== true) return null
  return positiveId(untrusted<CategoryDeleteResponseWire['deleted']>(body.deleted)?.id)
}

/** What the tree and the selects show for a node. */
export function categoryLabel(node: Pick<CategoryNode, 'name' | 'code'>): string {
  return node.name ?? node.code
}

// -- tree --------------------------------------------------------------------------------

export interface CategoryTreeNode {
  node: CategoryNode
  /** 0 for a root. */
  depth: number
  children: CategoryTreeNode[]
}

export interface CategoryTree {
  roots: CategoryTreeNode[]
  byId: ReadonlyMap<number, CategoryTreeNode>
}

/**
 * Nest the flat list by parentId, keeping the list's order among siblings.
 * No node is ever hidden: one whose parent is not in the list, or that sits in a parent
 * cycle (broken data), is shown at the top level.
 */
export function buildCategoryTree(nodes: readonly CategoryNode[]): CategoryTree {
  const byId = new Map<number, CategoryTreeNode>()
  for (const node of nodes) byId.set(node.id, { node, depth: 0, children: [] })
  const roots: CategoryTreeNode[] = []
  for (const entry of byId.values()) {
    const parentId = entry.node.parentId
    const parent = parentId !== null && parentId !== entry.node.id ? byId.get(parentId) : undefined
    if (parent) parent.children.push(entry)
    else roots.push(entry)
  }
  const placed = new Set<number>()
  const place = (entry: CategoryTreeNode, depth: number) => {
    placed.add(entry.node.id)
    entry.depth = depth
    entry.children = entry.children.filter(child => !placed.has(child.node.id))
    for (const child of entry.children) place(child, depth + 1)
  }
  for (const root of roots) place(root, 0)
  for (const entry of byId.values()) {
    if (placed.has(entry.node.id)) continue
    roots.push(entry)
    place(entry, 0)
  }
  return { roots, byId }
}

/** Every node in display order: a parent, then its subtree. */
export function flattenCategoryTree(tree: CategoryTree): CategoryTreeNode[] {
  const out: CategoryTreeNode[] = []
  const visit = (entry: CategoryTreeNode) => {
    out.push(entry)
    entry.children.forEach(visit)
  }
  tree.roots.forEach(visit)
  return out
}

/** The id itself and every id below it; empty when the tree has no such node. */
export function subtreeIds(tree: CategoryTree, id: number): Set<number> {
  const ids = new Set<number>()
  const visit = (entry: CategoryTreeNode | undefined) => {
    if (!entry) return
    ids.add(entry.node.id)
    entry.children.forEach(visit)
  }
  visit(tree.byId.get(id))
  return ids
}

/** The nodes above `id`, root first (the node itself is not included). */
export function categoryAncestors(tree: CategoryTree, id: number): CategoryNode[] {
  const chain: CategoryNode[] = []
  const seen = new Set<number>([id])
  let parentId = tree.byId.get(id)?.node.parentId ?? null
  while (parentId !== null && !seen.has(parentId)) {
    const parent = tree.byId.get(parentId)
    if (!parent) break
    seen.add(parentId)
    chain.unshift(parent.node)
    parentId = parent.node.parentId
  }
  return chain
}

export interface Inherited<T> {
  value: T
  /** The ancestor the value comes from; null when it is the node's own (or nobody set one). */
  from: CategoryNode | null
}

/** The cuisines that apply to a node: its own, else its nearest ancestor's that has any. */
export function effectiveCuisines(tree: CategoryTree, id: number): Inherited<string[]> {
  const own = tree.byId.get(id)?.node.cuisines ?? []
  if (own.length > 0) return { value: own, from: null }
  for (const ancestor of categoryAncestors(tree, id).reverse()) {
    if (ancestor.cuisines.length > 0) return { value: ancestor.cuisines, from: ancestor }
  }
  return { value: [], from: null }
}

/** The heating group that applies to a node: its own, else its nearest ancestor's. */
export function effectiveHeatingGroup(tree: CategoryTree, id: number): Inherited<HeatingGroupRef | null> {
  const own = tree.byId.get(id)?.node.heatingGroup ?? null
  if (own) return { value: own, from: null }
  for (const ancestor of categoryAncestors(tree, id).reverse()) {
    if (ancestor.heatingGroup) return { value: ancestor.heatingGroup, from: ancestor }
  }
  return { value: null, from: null }
}
