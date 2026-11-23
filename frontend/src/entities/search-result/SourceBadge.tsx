import type { SearchChannel, SearchRegion } from './model'

/**
 * Channel badge only when API mapped source; never invent. M6: flyb not product-success path.
 * `region`: 业务搜图's region search found the hit (the whole image had nothing over the threshold).
 */
export function SourceBadge({ source, region }: { source?: SearchChannel; region?: SearchRegion }) {
  if (source === 'self') {
    return region
      ? <span className="source-badge source-badge-self" title={`整图没有达到阈值的结果：查询图里检测到的「${region.label || '目标'}」区域（检测置信度 ${region.score.toFixed(2)}）搜到了这张图`}>自建 · 区域</span>
      : <span className="source-badge source-badge-self">自建</span>
  }
  // 业务搜图: the dish names its 菜谱识别 step recognised in the query image (text only, no image)
  if (source === 'recipe') return <span className="source-badge source-badge-recipe" title="菜谱识别在查询图的检测框里识别出的菜名，不是图库里的图片">菜谱识别</span>
  // 业务搜图: the self-built library had nothing, these came from its 现网 Global fallback
  if (source === 'global') return <span className="source-badge source-badge-global" title="自建库没有结果，来自现网 Global 兜底">Global</span>
  // Keep component branch for legacy responses; M6 tests do not require lighting this path.
  if (source === 'flyb') return <span className="source-badge source-badge-flyb">FlyB</span>
  return null
}
