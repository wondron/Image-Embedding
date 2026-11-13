import { categoryLabel, type CategoryName, type CategoryTree, type CategoryTreeNode } from '../../entities/category'

export type CategoryStatusFilter = 'all' | 'active' | 'inactive'

export interface CategoryTableFilter {
  /** Words matched against the name, every other name and the code. */
  query: string
  /** A root's id: only that domain's subtree; null: every root. */
  domainId: number | null
  status: CategoryStatusFilter
}

export const NO_CATEGORY_FILTER: CategoryTableFilter = { query: '', domainId: null, status: 'all' }

export interface CategoryTableRow {
  entry: CategoryTreeNode
  /** Indentation: 0 for a root. */
  depth: number
  /** Its children are listed right below it. */
  open: boolean
  /** Listed only because something below it matches the search. */
  context: boolean
}

export interface CategoryTableView {
  rows: CategoryTableRow[]
  /** Nodes in scope: the chosen domain's subtree, else the whole tree. */
  total: number
  /** Nodes the search and the status filter match; total when neither is set. */
  matched: number
  /** A search or a status filter is set: matches are shown with their ancestors, all opened. */
  filtering: boolean
}

function searchText({ node }: CategoryTreeNode): string {
  return [categoryLabel(node), node.code, ...node.names.map(name => name.name)].join('\n').toLocaleLowerCase()
}

/**
 * The rows the table lists, in tree order. Without a search or status filter, a node's children
 * follow it only when it is expanded. With one, every match is listed together with its
 * ancestors (marked as context), and nothing else; the ancestors are shown open.
 */
export function categoryTableView(
  tree: CategoryTree,
  filter: CategoryTableFilter,
  expanded: ReadonlySet<number>,
): CategoryTableView {
  const roots = filter.domainId === null ? tree.roots : tree.roots.filter(root => root.node.id === filter.domainId)
  const words = filter.query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  const filtering = words.length > 0 || filter.status !== 'all'
  const matches = new Set<number>()
  const keep = new Set<number>()
  let total = 0

  const scan = (entry: CategoryTreeNode): boolean => {
    total += 1
    const statusOk = filter.status === 'all' || (filter.status === 'active') === entry.node.isActive
    const self = statusOk && (words.length === 0 || words.every(word => searchText(entry).includes(word)))
    if (self) matches.add(entry.node.id)
    let below = false
    for (const child of entry.children) if (scan(child)) below = true
    if (self || below) keep.add(entry.node.id)
    return self || below
  }
  roots.forEach(scan)

  const rows: CategoryTableRow[] = []
  const walk = (entry: CategoryTreeNode, depth: number) => {
    const id = entry.node.id
    if (filtering && !keep.has(id)) return
    const children = filtering ? entry.children.filter(child => keep.has(child.node.id)) : entry.children
    const open = children.length > 0 && (filtering || expanded.has(id))
    rows.push({ entry, depth, open, context: filtering && !matches.has(id) })
    if (open) children.forEach(child => walk(child, depth + 1))
  }
  roots.forEach(root => walk(root, 0))

  return { rows, total, matched: filtering ? matches.size : total, filtering }
}

/** Every name but the zh primary one (that one is the label): aliases and other languages. */
export function otherCategoryNames(names: readonly CategoryName[]): CategoryName[] {
  const primary = names.find(name => name.lang === 'zh' && name.kind === 'primary')
  return names.filter(name => name !== primary)
}

/**
 * The code a new category gets when its parent changes in the form: what was typed after the old
 * parent's prefix moves under the new one ("dish.pizza" → "ingredient.pizza"). Untouched and
 * custom codes are handled as the user would expect: an empty or prefix-only code becomes the
 * new prefix; a code that does not start with the old prefix is kept.
 */
export function retargetCode(code: string, oldPrefix: string, newPrefix: string): string {
  const codeKey = code.toLowerCase()
  const prefixKey = oldPrefix.toLowerCase()
  if (code === '' || codeKey === prefixKey) return newPrefix
  if (oldPrefix !== '' && codeKey.startsWith(prefixKey)) return newPrefix + code.slice(oldPrefix.length)
  return code
}
