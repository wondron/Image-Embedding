import { EnumConfigPanel } from '../../features/enum-config'

/** Route entry for 枚举配置. The shell title stays in the top bar. */
export function EnumConfigPage({ apiBase = '' }: { apiBase?: string }) {
  return <EnumConfigPanel apiBase={apiBase} />
}
