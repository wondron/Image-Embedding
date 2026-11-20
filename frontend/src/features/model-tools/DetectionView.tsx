import { useMemo, useState, type CSSProperties } from 'react'
import { ImageStage } from './ImageStage'
import { PALETTE } from './mask'
import type { DetectionResult } from './model'

const NO_LABEL = '（没有匹配的词）'
const percent = (value: number, total: number) => `${(value / total) * 100}%`

/** GroundingDINO's boxes drawn over the image and listed below it; hovering a row highlights its box. */
export function DetectionView({ result, imageUrl }: { result: DetectionResult; imageUrl: string }) {
  const [active, setActive] = useState<number | null>(null)
  const { width, height, detections } = result
  const labels = useMemo(() => [...new Set(detections.map(item => item.label))], [detections])
  const colorOf = (label: string) => ({ '--c': PALETTE[labels.indexOf(label) % PALETTE.length].join(' ') }) as CSSProperties
  const stateOf = (index: number) => (active === null ? '' : active === index ? 'is-active' : 'is-dim')

  return <div className="mt-view">
    <dl className="mt-summary">
      <div><dt>检测框</dt><dd>{detections.length}</dd></div>
      <div><dt>caption</dt><dd><code>{result.caption}</code></dd></div>
      <div><dt>box_threshold</dt><dd>{result.boxThreshold}</dd></div>
      <div><dt>text_threshold</dt><dd>{result.textThreshold}</dd></div>
      <div><dt>图片</dt><dd>{width} × {height}</dd></div>
      <div><dt>耗时</dt><dd>{(result.latencyMs / 1000).toFixed(2)} s</dd></div>
    </dl>
    <ImageStage width={width} height={height} imageUrl={imageUrl} alt="检测的图片">
      <svg className="mt-layer mt-boxes" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
        {detections.map((item, index) => {
          const [x1, y1, x2, y2] = item.box
          return <rect key={index} className={stateOf(index)} style={colorOf(item.label)}
            x={x1} y={y1} width={x2 - x1} height={y2 - y1} vectorEffect="non-scaling-stroke" />
        })}
      </svg>
      {detections.map((item, index) => {
        const [x1, y1] = item.box
        // A box at the top edge has no room above it: its label goes inside
        const inside = y1 / height < 0.08
        return <span key={index} className={`mt-box-label ${inside ? 'is-inside' : ''} ${stateOf(index)}`}
          style={{ ...colorOf(item.label), left: percent(x1, width), top: percent(y1, height) }}>
          {item.label || NO_LABEL} {item.score.toFixed(2)}
        </span>
      })}
    </ImageStage>
    {detections.length > 0
      ? <table className="mt-table" aria-label="检测框">
        <thead><tr><th>#</th><th>词</th><th>置信度</th><th>位置 x1, y1, x2, y2</th><th>大小</th></tr></thead>
        <tbody>{detections.map((item, index) => {
          const [x1, y1, x2, y2] = item.box
          return <tr key={index} className={active === index ? 'is-active' : ''} onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)}>
            <td>{index + 1}</td>
            <td><span className="mt-swatch" style={colorOf(item.label)} />{item.label || NO_LABEL}</td>
            <td>{item.score.toFixed(4)}</td>
            <td className="monospace">{x1}, {y1}, {x2}, {y2}</td>
            <td>{x2 - x1} × {y2 - y1}</td>
          </tr>
        })}</tbody>
      </table>
      : <p className="mt-empty-note" role="status">没有检测到目标：所有框的置信度都低于 box_threshold（{result.boxThreshold}）。可以调低阈值，或换个说法再试。</p>}
  </div>
}
