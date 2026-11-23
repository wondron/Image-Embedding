import { ModelToolsPanel } from '../../features/model-tools'

/**
 * Route entry for 模型工具 (?view=tools&tool=…). The shell title stays in the top bar. `initialFile`:
 * the image to open the tools with (a 业务搜图 query sent here from its results).
 */
export function ToolsPage({ apiBase = '', initialFile = null }: { apiBase?: string; initialFile?: File | null }) {
  return <ModelToolsPanel apiBase={apiBase} initialFile={initialFile} />
}
