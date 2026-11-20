import { ModelToolsPanel } from '../../features/model-tools'

/** Route entry for 模型工具 (?view=tools). The shell title stays in the top bar. */
export function ToolsPage({ apiBase = '' }: { apiBase?: string }) {
  return <ModelToolsPanel apiBase={apiBase} />
}
