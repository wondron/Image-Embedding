import type { CategoryTree, CategoryTreeNode, HeatingGroupRef } from '../../entities/category'

interface ExportCategory {
  id: number
  parent_id: number | null
  code: string
  path: string
  name: string | null
  names: { lang: string; name: string; kind: 'primary' | 'alias' }[]
  cuisines: string[]
  heating_group: HeatingGroupRef | null
  sort_order: number
  is_active: boolean
  image_count: number
  scene_rule_count: number
  created_at: string | null
  updated_at: string | null
  children: ExportCategory[]
}

export interface CategoryTreeExportFile {
  filename: string
  content: string
  count: number
}

function exportCategory({ node, children }: CategoryTreeNode): ExportCategory {
  return {
    id: node.id,
    parent_id: node.parentId,
    code: node.code,
    path: node.path,
    name: node.name,
    names: node.names.map(name => ({ ...name })),
    cuisines: [...node.cuisines],
    heating_group: node.heatingGroup ? { ...node.heatingGroup } : null,
    sort_order: node.sortOrder,
    is_active: node.isActive,
    image_count: node.imageCount,
    scene_rule_count: node.sceneRuleCount,
    created_at: node.createdAt,
    updated_at: node.updatedAt,
    children: children.map(exportCategory),
  }
}

/** Export the complete tree, independently of the table's filters and expanded rows. */
export function buildCategoryTreeExport(
  tree: CategoryTree,
  rootCodes: readonly string[],
  heatingGroups: readonly HeatingGroupRef[],
  exportedAt = new Date(),
): CategoryTreeExportFile {
  const timestamp = exportedAt.toISOString()
  const payload = {
    schema_version: 1,
    exported_at: timestamp,
    category_count: tree.byId.size,
    root_codes: [...rootCodes],
    heating_groups: heatingGroups.map(group => ({ ...group })),
    categories: tree.roots.map(exportCategory),
  }
  return {
    filename: `category-tree-${timestamp.replace(/[:.]/g, '-')}.json`,
    content: `${JSON.stringify(payload, null, 2)}\n`,
    count: payload.category_count,
  }
}

/** Hand the JSON file to the browser; the browser controls its final save location. */
export function downloadCategoryTree(file: Pick<CategoryTreeExportFile, 'filename' | 'content'>): void {
  const anchor = document.createElement('a')
  const url = URL.createObjectURL(new Blob([file.content], { type: 'application/json;charset=utf-8' }))
  try {
    anchor.href = url
    anchor.download = file.filename
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
