import { categoryAncestors, categoryLabel, flattenCategoryTree, type CategoryTree } from '../../entities/category'

/** One category as a picker lists it. */
export interface CategoryOption {
  id: number
  code: string
  /** The zh primary name, else the code (categoryLabel). */
  label: string
  /** Ancestor labels from the root, e.g. 「菜品 / 肉类」; '' for a root. */
  path: string
  depth: number
  isActive: boolean
  /** Lower-cased search text: label, code, every name (aliases, other languages) and the path. */
  haystack: string
}

/** Every node in tree order (a parent, then its subtree). */
export function categoryOptions(tree: CategoryTree): CategoryOption[] {
  return flattenCategoryTree(tree).map(({ node, depth }) => {
    const ancestors = categoryAncestors(tree, node.id).map(categoryLabel)
    const label = categoryLabel(node)
    return {
      id: node.id,
      code: node.code,
      label,
      path: ancestors.join(' / '),
      depth,
      isActive: node.isActive,
      haystack: [label, node.code, ...node.names.map(name => name.name), ...ancestors].join('\n').toLocaleLowerCase(),
    }
  })
}

/** 「菜品 / 肉类 / 牛排」: the node's labels from the root; null when the tree has no such node. */
export function categoryPath(tree: CategoryTree, id: number): string | null {
  const entry = tree.byId.get(id)
  if (!entry) return null
  return [...categoryAncestors(tree, id), entry.node].map(categoryLabel).join(' / ')
}

function rank(option: CategoryOption, query: string): number {
  const label = option.label.toLocaleLowerCase()
  if (label === query) return 0
  if (label.startsWith(query)) return 1
  if (label.includes(query)) return 2
  if (option.code.toLocaleLowerCase().startsWith(query)) return 3
  return 4
}

/**
 * The active options that match every whitespace-separated word of the query (in the label, the
 * code, any name or the path). An empty query keeps every active option in tree order. Best
 * matches first: label equal to the query, starting with it, containing it, code prefix, the rest;
 * ties keep tree order.
 */
export function filterCategoryOptions(options: readonly CategoryOption[], query: string): CategoryOption[] {
  const active = options.filter(option => option.isActive)
  const q = query.trim().toLocaleLowerCase()
  if (!q) return active
  const words = q.split(/\s+/)
  return active
    .filter(option => words.every(word => option.haystack.includes(word)))
    .map((option, index) => ({ option, index, rank: rank(option, q) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map(entry => entry.option)
}
