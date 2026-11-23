import { useEffect, useState } from 'react'
import { ImageIcon } from 'lucide-react'
import type { SearchRegion } from '../../entities/search-result'

/**
 * 业务搜图's region search: the query image with the box whose crop found the hit outlined and the
 * rest dimmed. `source`: the file searched with, or the link the server downloaded (the browser
 * loads it too; it may refuse).
 */
export function QueryRegionPreview({ source, region }: { source: File | string; region: SearchRegion }) {
  const [url, setUrl] = useState<string | null>(typeof source === 'string' ? source : null)
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setNatural(null)
    setFailed(false)
    if (typeof source === 'string') { setUrl(source); return }
    const objectUrl = URL.createObjectURL(source)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [source])

  if (failed || !url) {
    return <div className="query-region-unavailable" role="status"><ImageIcon size={22} strokeWidth={1.4} aria-hidden /><span>查询图预览暂不可用</span></div>
  }
  const [x1, y1, x2, y2] = region.box
  // The box is in the pixels the server decoded; the browser shows the same unless it turned the photo by its EXIF orientation
  const outside = natural !== null && (x2 > natural.width || y2 > natural.height)
  return <figure className="query-region">
    <div className="query-region-stage">
      <img src={url} alt="查询图" referrerPolicy="no-referrer" decoding="async"
        onLoad={event => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
        onError={() => setFailed(true)} />
      {natural && <svg viewBox={`0 0 ${natural.width} ${natural.height}`} preserveAspectRatio="none" role="img" aria-label="查询图上搜到这张图的区域">
        <path className="query-region-dim" fillRule="evenodd" d={`M0 0H${natural.width}V${natural.height}H0Z M${x1} ${y1}H${x2}V${y2}H${x1}Z`} />
        <rect className="query-region-box" x={x1} y={y1} width={x2 - x1} height={y2 - y1} vectorEffect="non-scaling-stroke" />
      </svg>}
    </div>
    {outside && <figcaption className="query-region-warning">这个框超出了浏览器显示的图片（多半是照片里记录的拍摄方向），位置可能对不上。</figcaption>}
  </figure>
}
