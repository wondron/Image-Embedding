import { CategoryConfigPanel } from '../../features/category-config'

/** Route entry for 类别配置. The shell title stays in the top bar. */
export function CategoryConfigPage({ apiBase = '' }: { apiBase?: string }) {
  return <CategoryConfigPanel apiBase={apiBase} />
}
