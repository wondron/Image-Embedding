import { flattenCategoryTree, type CategoryTree, type CategoryTreeNode } from '../../entities/category'

/**
 * Counts over the category tree, for the footer of 食材管理 and the downloaded topology:
 * how the tree is built (domains, groups, leaves) and where the images are labelled.
 */

/** A leaf with fewer images than this has too few to search or train with. */
export const FEW_IMAGES = 5

/** A root is a domain; below the roots, a node with children is a group and one without is a leaf. */
export type CategoryRole = 'domain' | 'group' | 'leaf'

export function categoryRole(entry: CategoryTreeNode): CategoryRole {
  if (entry.depth === 0) return 'domain'
  return entry.children.length > 0 ? 'group' : 'leaf'
}

export interface CategoryStats {
  /** domains + groups + leaves. */
  total: number
  domains: number
  groups: number
  leaves: number
  inactive: number
  /** Levels from the top of what was counted down to its deepest node; 0 when nothing was counted. */
  levels: number
  /** Images labelled with any of the nodes (each image has one category). */
  images: number
  /** Images on a leaf. */
  leafImages: number
  /** Images on a domain or a group: labels that stop above the leaves. */
  coarseImages: number
  /** Domains and groups with images of their own. */
  coarseCategories: number
  /** Leaves without images. */
  emptyLeaves: number
  /** Leaves with 1 to FEW_IMAGES - 1 images. */
  fewImageLeaves: number
}

export function categoryStats(entries: Iterable<CategoryTreeNode>): CategoryStats {
  const stats: CategoryStats = {
    total: 0, domains: 0, groups: 0, leaves: 0, inactive: 0, levels: 0,
    images: 0, leafImages: 0, coarseImages: 0, coarseCategories: 0, emptyLeaves: 0, fewImageLeaves: 0,
  }
  let top = Infinity
  let bottom = -Infinity
  for (const entry of entries) {
    const images = entry.node.imageCount
    stats.total += 1
    if (!entry.node.isActive) stats.inactive += 1
    stats.images += images
    top = Math.min(top, entry.depth)
    bottom = Math.max(bottom, entry.depth)
    const role = categoryRole(entry)
    if (role === 'leaf') {
      stats.leaves += 1
      stats.leafImages += images
      if (images === 0) stats.emptyLeaves += 1
      else if (images < FEW_IMAGES) stats.fewImageLeaves += 1
      continue
    }
    if (role === 'domain') stats.domains += 1
    else stats.groups += 1
    stats.coarseImages += images
    if (images > 0) stats.coarseCategories += 1
  }
  stats.levels = stats.total === 0 ? 0 : bottom - top + 1
  return stats
}

/** The nodes in scope: the subtree of `scopeId` (none when it is not in the tree), else the whole tree. */
export function scopeEntries(tree: CategoryTree, scopeId: number | null): CategoryTreeNode[] {
  if (scopeId === null) return flattenCategoryTree(tree)
  const root = tree.byId.get(scopeId)
  if (!root) return []
  const out: CategoryTreeNode[] = []
  const visit = (entry: CategoryTreeNode) => {
    out.push(entry)
    entry.children.forEach(visit)
  }
  visit(root)
  return out
}

/** A group of categories the footer counts and the table can be narrowed to. */
export type CategoryFocus = 'empty_leaves' | 'few_image_leaves' | 'coarse_images'

export function matchesFocus(entry: CategoryTreeNode, focus: CategoryFocus): boolean {
  const images = entry.node.imageCount
  const leaf = categoryRole(entry) === 'leaf'
  switch (focus) {
    case 'empty_leaves': return leaf && images === 0
    case 'few_image_leaves': return leaf && images > 0 && images < FEW_IMAGES
    case 'coarse_images': return !leaf && images > 0
  }
}
