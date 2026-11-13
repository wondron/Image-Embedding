import { CategoryConfigPanel } from '../../features/category-config'

/** Route entry for 食物管理 (?view=categories): the category tree. The shell title stays in the top bar. */
export function CategoryConfigPage({ apiBase = '' }: { apiBase?: string }) {
  return <CategoryConfigPanel apiBase={apiBase} />
}
