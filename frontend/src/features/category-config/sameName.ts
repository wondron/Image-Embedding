import { categoryAncestors, categoryLabel, type CategoryNode, type CategoryTree } from '../../entities/category'

/** A category that already carries a name being typed. */
export interface SameNameMatch {
  node: CategoryNode
  /** 「食材 / 薯类 / 红薯」 */
  path: string
  /** The alias (or other-language name) that matches, when it is not the category's label. */
  alias: string | null
}

/**
 * The categories that already carry this name, as their name or as one of their aliases, ignoring
 * case and surrounding blanks. Names may repeat across the tree, so this is a hint for the person
 * (with hundreds of categories it is easy to add 番薯 while 红薯 already lists it as an alias),
 * not a rule. `exceptId` leaves out the category being edited.
 */
export function sameNameCategories(tree: CategoryTree, name: string, exceptId?: number): SameNameMatch[] {
  const wanted = name.trim().toLocaleLowerCase()
  if (!wanted) return []
  const matches: SameNameMatch[] = []
  for (const { node } of tree.byId.values()) {
    if (node.id === exceptId) continue
    const label = categoryLabel(node)
    const hit = label.toLocaleLowerCase() === wanted
      ? label
      : node.names.find(entry => entry.name.trim().toLocaleLowerCase() === wanted)?.name
    if (hit === undefined) continue
    matches.push({
      node,
      path: [...categoryAncestors(tree, node.id), node].map(categoryLabel).join(' / '),
      alias: hit === label ? null : hit,
    })
  }
  return matches
}
