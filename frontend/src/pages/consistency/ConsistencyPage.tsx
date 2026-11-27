import { ConsistencyPanel, type FixView } from '../../features/consistency-check'

/** Route entry for 数据核对 (?view=consistency). The shell title stays in the top bar. */
export function ConsistencyPage({ apiBase = '', onOpen }: {
  apiBase?: string
  onOpen?: (view: FixView, params?: Readonly<Record<string, string>>) => void
}) {
  return <ConsistencyPanel apiBase={apiBase} onOpen={onOpen} />
}
