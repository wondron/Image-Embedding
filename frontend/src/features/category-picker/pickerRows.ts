import type { CategoryNode, CategoryTree, CategoryTreeNode } from '../../entities/category'

/** Which categories a picker offers. */
export interface PickerScope {
  /**
   * Offer disabled categories too (a parent or a rule target may be one). Otherwise a disabled
   * category is listed only above an enabled one, and cannot be picked itself.
   */
  includeInactive?: boolean
  /** Left out together with everything below them, e.g. the node being moved. */
  exclude?: ReadonlySet<number>
}

/** What a scope leaves of the tree; computed once per tree and scope. O(nodes). */
export interface PickerIndex {
  /** Ids that can be picked. */
  pickable: ReadonlySet<number>
  /** The listed roots: pickable, or above something pickable. */
  roots: readonly CategoryTreeNode[]
  /** The listed children of each listed node. */
  children: ReadonlyMap<number, readonly CategoryTreeNode[]>
  /** How many categories can be picked below each listed node, at every level. */
  below: ReadonlyMap<number, number>
}

export function pickerIndex(tree: CategoryTree, scope: PickerScope = {}): PickerIndex {
  const pickable = new Set<number>()
  const children = new Map<number, CategoryTreeNode[]>()
  const below = new Map<number, number>()

  /** How many categories can be picked on the node and below it; null when the scope leaves it out. */
  const scan = (entry: CategoryTreeNode): number | null => {
    const { node } = entry
    if (scope.exclude?.has(node.id)) return null
    const offered = scope.includeInactive === true || node.isActive
    const listed: CategoryTreeNode[] = []
    let count = 0
    for (const child of entry.children) {
      const inChild = scan(child)
      if (inChild === null) continue
      listed.push(child)
      count += inChild
    }
    if (!offered && count === 0) return null
    if (offered) pickable.add(node.id)
    children.set(node.id, listed)
    below.set(node.id, count)
    return count + (offered ? 1 : 0)
  }
  const roots = tree.roots.filter(root => scan(root) !== null)
  return { pickable, roots, children, below }
}

/** One line of a picker's tree. */
export interface PickerRow {
  node: CategoryNode
  /** 0 for a root. */
  depth: number
  parentId: number | null
  /** false for a disabled category listed only for what is below it. */
  pickable: boolean
  /** It has listed children. */
  hasChildren: boolean
  /** Its listed children follow it. */
  open: boolean
  /** Categories that can be picked below it. */
  below: number
  /** 1-based position among its listed siblings and their number (aria-posinset / aria-setsize). */
  position: number
  siblings: number
}

/** The lines to show, in tree order: a node's children follow it only while it is open. */
export function pickerRows(index: PickerIndex, isOpen: (id: number) => boolean): PickerRow[] {
  const rows: PickerRow[] = []
  const walk = (entry: CategoryTreeNode, depth: number, parentId: number | null, position: number, siblings: number) => {
    const id = entry.node.id
    const listed = index.children.get(id) ?? []
    const open = listed.length > 0 && isOpen(id)
    rows.push({
      node: entry.node, depth, parentId, pickable: index.pickable.has(id), hasChildren: listed.length > 0, open,
      below: index.below.get(id) ?? 0, position, siblings,
    })
    if (open) listed.forEach((child, at) => walk(child, depth + 1, id, at + 1, listed.length))
  }
  index.roots.forEach((root, at) => walk(root, 0, null, at + 1, index.roots.length))
  return rows
}
