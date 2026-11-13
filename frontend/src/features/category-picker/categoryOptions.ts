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
  /** Every name but the label: aliases and names in other languages, as stored. */
  otherNames: string[]
  /** Lower-cased search text: label, code, every name (aliases, other languages) and the path. */
  haystack: string
}

/** Every node in tree order (a parent, then its subtree). */
export function categoryOptions(tree: CategoryTree): CategoryOption[] {
  return flattenCategoryTree(tree).map(({ node, depth }) => {
    const ancestors = categoryAncestors(tree, node.id).map(categoryLabel)
    const label = categoryLabel(node)
    const names = node.names.map(name => name.name)
    return {
      id: node.id,
      code: node.code,
      label,
      path: ancestors.join(' / '),
      depth,
      isActive: node.isActive,
      otherNames: [...new Set(names.filter(name => name !== label))],
      haystack: [label, node.code, ...names, ...ancestors].join('\n').toLocaleLowerCase(),
    }
  })
}

/** 「菜品 / 肉类 / 牛排」: the node's labels from the root; null when the tree has no such node. */
export function categoryPath(tree: CategoryTree, id: number): string | null {
  const entry = tree.byId.get(id)
  if (!entry) return null
  return [...categoryAncestors(tree, id), entry.node].map(categoryLabel).join(' / ')
}

/** The last segment of a dotted code: what the category itself adds to its parent's code. */
export function codeTail(code: string): string {
  return code.slice(code.lastIndexOf('.') + 1)
}

function rank(option: CategoryOption, query: string): number {
  const label = option.label.toLocaleLowerCase()
  if (label === query) return 0
  const others = option.otherNames.map(name => name.toLocaleLowerCase())
  if (others.includes(query)) return 1
  if (label.startsWith(query)) return 2
  if (label.includes(query)) return 3
  if (others.some(name => name.includes(query))) return 4
  const code = option.code.toLocaleLowerCase()
  if (code.startsWith(query) || codeTail(code).startsWith(query)) return 5
  return 6
}

/**
 * The options that match every whitespace-separated word of the query (in the label, the code, any
 * name or the path). `pickable` says which options are offered at all (see pickerIndex); without
 * it the active ones are. An empty query keeps every offered option in tree order. Best matches
 * first: label equal to the query, another name equal to it, label starting with it, containing
 * it, another name containing it, code prefix, the rest; ties keep tree order.
 */
export function filterCategoryOptions(
  options: readonly CategoryOption[],
  query: string,
  pickable?: ReadonlySet<number>,
): CategoryOption[] {
  const offered = options.filter(option => (pickable ? pickable.has(option.id) : option.isActive))
  const q = query.trim().toLocaleLowerCase()
  if (!q) return offered
  const words = q.split(/\s+/)
  return offered
    .filter(option => words.every(word => option.haystack.includes(word)))
    .map((option, index) => ({ option, index, rank: rank(option, q) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map(entry => entry.option)
}

/**
 * Why a search found an option whose label and code do not contain what was typed: the alias (or
 * name in another language) that does, e.g. 「地瓜」 for 红薯. null when the label or the code
 * explains every word, or only the path matched.
 */
export function matchedOtherName(option: CategoryOption, query: string): string | null {
  const label = option.label.toLocaleLowerCase()
  const code = option.code.toLocaleLowerCase()
  for (const word of query.trim().toLocaleLowerCase().split(/\s+/)) {
    if (!word || label.includes(word) || code.includes(word)) continue
    const name = option.otherNames.find(other => other.toLocaleLowerCase().includes(word))
    if (name) return name
  }
  return null
}
