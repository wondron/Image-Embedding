import type { SearchChannel } from './model'

/** Channel badge only when API mapped source; never invent. M6: flyb not product-success path. */
export function SourceBadge({ source }: { source?: SearchChannel }) {
  if (source === 'self') return <span className="source-badge source-badge-self">自建</span>
  // 业务搜图: the self-built library had nothing, these came from its 现网 Global fallback
  if (source === 'global') return <span className="source-badge source-badge-global" title="自建库没有结果，来自现网 Global 兜底">Global</span>
  // Keep component branch for legacy responses; M6 tests do not require lighting this path.
  if (source === 'flyb') return <span className="source-badge source-badge-flyb">FlyB</span>
  return null
}
