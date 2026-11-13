import { categoryLabel, type CategoryNode, type CategoryTree, type CategoryTreeNode } from '../../entities/category'

// The navigator's counts; the category table shows the same ones (entities/category)
export { subtreeImageCounts } from '../../entities/category'

export interface CategoryNavOptions {
  /** Whitespace-separated words, matched against names, codes and aliases (see categoryNavView). */
  query: string
  /** Opened nodes. Ignored while searching: every match is listed with its ancestors open. */
  expanded: ReadonlySet<number>
  /** Leave out the categories without any image on them or below them. */
  hideEmpty: boolean
  /** List disabled categories too; otherwise one is listed only above an enabled one. */
  showInactive: boolean
}

export interface CategoryNavRow {
  node: CategoryNode
  /** 0 for a root. */
  depth: number
  parentId: number | null
  /** Images on it or below it, every scene. */
  count: number
  /** It has listed children (under the options and the search). */
  hasChildren: boolean
  /** Its listed children follow it. */
  open: boolean
  /** Listed only for what is below it: the ancestor of a match, or a disabled parent. */
  context: boolean
  /** 1-based position among its listed siblings and their number (aria-posinset / aria-setsize). */
  position: number
  siblings: number
}

export interface CategoryNavView {
  rows: CategoryNavRow[]
  searching: boolean
  /** Categories the search matches and the options keep; 0 without a search. */
  matched: number
  /** Categories the search matches that the options leave out (no images, or disabled). */
  hidden: number
}

export function searchWords(query: string): string[] {
  return query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
}

function ownText(node: CategoryNode): string {
  return [categoryLabel(node), node.code, ...node.names.map(name => name.name)].join('\n').toLocaleLowerCase()
}

/**
 * Every word is in the node's own text or in its ancestors' names, and at least one is in its own:
 * 「肉 牛」 finds 牛排 under 肉类, while 「菜品」 finds the 菜品 node, not all of its subtree.
 */
function matches(own: string, ancestors: string, words: readonly string[]): boolean {
  let ownHit = false
  for (const word of words) {
    if (own.includes(word)) ownHit = true
    else if (!ancestors.includes(word)) return false
  }
  return ownHit
}

/**
 * The rows the category navigator lists, in tree order. Without a search a node's children follow
 * it only when it is expanded; with one, the matches are listed with their ancestors (as context),
 * all open, and nothing else. The options apply in both cases. O(nodes).
 */
export function categoryNavView(tree: CategoryTree, counts: ReadonlyMap<number, number>, options: CategoryNavOptions): CategoryNavView {
  const words = searchWords(options.query)
  const searching = words.length > 0
  let matched = 0
  let hidden = 0
  const listedChildren = new Map<number, CategoryTreeNode[]>()
  const shownForItself = new Set<number>()

  const scan = (entry: CategoryTreeNode, ancestors: string): boolean => {
    const { node } = entry
    const kept = (options.showInactive || node.isActive) && (!options.hideEmpty || (counts.get(node.id) ?? 0) > 0)
    let self = kept
    if (searching) {
      const hit = matches(ownText(node), ancestors, words)
      if (hit && kept) matched += 1
      else if (hit) hidden += 1
      self = hit && kept
    }
    const below = `${ancestors}\n${categoryLabel(node).toLocaleLowerCase()}`
    const children = entry.children.filter(child => scan(child, below))
    listedChildren.set(node.id, children)
    if (self) shownForItself.add(node.id)
    return self || children.length > 0
  }
  const roots = tree.roots.filter(root => scan(root, ''))

  const rows: CategoryNavRow[] = []
  const walk = (entry: CategoryTreeNode, depth: number, parentId: number | null, position: number, siblings: number) => {
    const id = entry.node.id
    const children = listedChildren.get(id) ?? []
    const open = children.length > 0 && (searching || options.expanded.has(id))
    rows.push({
      node: entry.node, depth, parentId, count: counts.get(id) ?? 0,
      hasChildren: children.length > 0, open, context: !shownForItself.has(id), position, siblings,
    })
    if (open) children.forEach((child, index) => walk(child, depth + 1, id, index + 1, children.length))
  }
  roots.forEach((root, index) => walk(root, 0, null, index + 1, roots.length))
  return { rows, searching, matched, hidden }
}

export interface TextPart {
  text: string
  hit: boolean
}

/** The text cut into the parts that match a search word and the rest, for highlighting. */
export function highlightParts(text: string, words: readonly string[]): TextPart[] {
  const lower = text.toLocaleLowerCase()
  // Lower-casing can change the length of a few characters; then the offsets would not line up
  if (!words.length || lower.length !== text.length) return [{ text, hit: false }]
  const marked = new Array<boolean>(text.length).fill(false)
  for (const word of words) {
    if (!word) continue  // indexOf('') never moves on
    for (let at = lower.indexOf(word); at >= 0; at = lower.indexOf(word, at + word.length)) {
      marked.fill(true, at, at + word.length)
    }
  }
  const parts: TextPart[] = []
  let start = 0
  for (let index = 1; index <= text.length; index += 1) {
    if (index === text.length || marked[index] !== marked[start]) {
      parts.push({ text: text.slice(start, index), hit: marked[start] })
      start = index
    }
  }
  return parts.length ? parts : [{ text, hit: false }]
}
