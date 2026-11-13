import { ModelVersionsPanel } from '../../features/model-versions'

/** Route entry for 模型版本. The shell title stays in the top bar. */
export function ModelVersionsPage({ apiBase = '' }: { apiBase?: string }) {
  return <ModelVersionsPanel apiBase={apiBase} />
}
