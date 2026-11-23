import { useId, useMemo, useState, type CSSProperties } from 'react'
import { CircleAlert, CircleCheck, CircleSlash, RotateCcw, ScanSearch } from 'lucide-react'
import { ImageStage } from './ImageStage'
import { dishNames, formatThreshold, isRecognised, thresholdFrom, type RecipeCandidate, type RecipeResult } from './model'

/** Box colours: the closest dish passes the threshold (业务搜图 returns it) or not. */
const ACCEPTED = '22 163 74'
const REJECTED = '100 116 139'
const NO_LABEL = '（没有匹配的词）'
/** Below 16 px a side, 业务搜图 drops a box (region_search.MIN_REGION_SIDE). */
const MIN_REGION_SIDE = 16
/** The slider's grain: three decimals of cosine similarity. */
const STEP = 0.001

const percent = (value: number, total: number) => `${(value / total) * 100}%`
const colour = (accepted: boolean) => ({ '--c': accepted ? ACCEPTED : REJECTED }) as CSSProperties
const signed = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : '±'}${Math.abs(value).toFixed(4)}`

/**
 * What the recipe model is given for one box: its crop scaled to fit a square, black around it —
 * the model's own letterbox (224 × 224). The whole image when there is no box.
 */
function ModelInput({ imageUrl, width, height, box, label }: {
  imageUrl: string
  width: number
  height: number
  box?: readonly [number, number, number, number]
  label: string
}) {
  const [x1, y1, x2, y2] = box ?? [0, 0, width, height]
  const cropWidth = x2 - x1
  const cropHeight = y2 - y1
  // the crop's place in a 100 × 100 square, long side filling it
  const scale = 100 / Math.max(cropWidth, cropHeight)
  const inner = { width: cropWidth * scale, height: cropHeight * scale }
  return <svg className="rt-input" viewBox="0 0 100 100" role="img" aria-label={label}>
    <rect width="100" height="100" fill="#000" />
    <svg x={(100 - inner.width) / 2} y={(100 - inner.height) / 2} width={inner.width} height={inner.height}
      viewBox={`${x1} ${y1} ${cropWidth} ${cropHeight}`} preserveAspectRatio="none">
      <image href={imageUrl} width={width} height={height} preserveAspectRatio="none" />
    </svg>
  </svg>
}

/** A score on a 0–1 bar with the threshold marked, and how far above or below it is. */
function ScoreAgainst({ score, threshold }: { score: number; threshold: number }) {
  const above = score > threshold
  return <span className="rt-score">
    <span className="rt-meter" aria-hidden>
      <span className={`rt-meter-fill ${above ? 'is-above' : ''}`} style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }} />
      <span className="rt-meter-mark" style={{ left: `${threshold * 100}%` }} />
    </span>
    <span className="rt-score-value">{score.toFixed(4)}</span>
    <small className={`rt-delta ${above ? 'is-above' : 'is-below'}`} title={`和阈值 ${formatThreshold(threshold)} 相差`}>{signed(score - threshold)}</small>
  </span>
}

/** The row of each name 业务搜图 keeps: a name is returned once, at its best box (the first on a tie). */
function keptRows(closest: ReadonlyArray<RecipeCandidate | undefined>, threshold: number): Set<number> {
  const best = new Map<string, number>()
  closest.forEach((candidate, index) => {
    if (!candidate || !isRecognised(candidate, threshold)) return
    const kept = best.get(candidate.label)
    if (kept === undefined || candidate.score > closest[kept]!.score) best.set(candidate.label, index)
  })
  return new Set(best.values())
}

/** What 业务搜图 does with this image at the threshold tried. */
function Outcome({ result, threshold, names }: { result: RecipeResult; threshold: number; names: string[] }) {
  const { regions, detected } = result
  if (!regions.length) {
    return <p className="rt-outcome is-empty" role="status"><CircleAlert size={16} aria-hidden />
      <span><b>没有可识别的检测框，业务搜图不会给出菜谱卡</b>：它只识别检测框，不会拿整张图去识别。
        {detected > 0 ? `GroundingDINO 给了 ${detected} 个框，都因边长小于 ${MIN_REGION_SIDE} 像素被丢掉。` : '可以调低 box_threshold，或换个 caption 再试。'}</span>
    </p>
  }
  if (names.length) {
    return <p className="rt-outcome is-accepted" role="status"><CircleCheck size={16} aria-hidden />
      <span><b>业务搜图会返回一张菜谱卡：</b><span className="rt-names">{names.map(name => <span key={name} className="rt-name">{name}</span>)}</span>
        <small>（整图搜索没有结果时才会用到；每个框最多一个菜名，同名只留一个，按相似度从高到低）</small></span>
    </p>
  }
  // nothing passed: the box that came closest
  const closest = regions
    .map((region, index) => ({ index, candidate: region.candidates[0] }))
    .filter((entry): entry is { index: number; candidate: RecipeCandidate } => entry.candidate !== undefined)
    .sort((a, b) => b.candidate.score - a.candidate.score)[0]
  return <p className="rt-outcome" role="status"><CircleSlash size={16} aria-hidden />
    <span><b>没有框过阈值 {formatThreshold(threshold)}，业务搜图不会给出菜谱卡。</b>
      {closest && <>最接近的是 #{closest.index + 1} 的「{closest.candidate.label}」{closest.candidate.score.toFixed(4)}，还差 {(threshold - closest.candidate.score).toFixed(4)}。</>}</span>
  </p>
}

/**
 * 菜谱识别 as 业务搜图 runs it: GroundingDINO's boxes (the ones it keeps) over the image, coloured by
 * whether their crop's closest dish passes the threshold; per box what the model was given, its
 * closest dishes and the verdict; the whole image's closest dishes for comparison. The threshold
 * can be tried here: the verdicts are worked out again from the scores, without a new call.
 */
export function RecipeView({ result, imageUrl, threshold, onThresholdChange }: {
  result: RecipeResult
  imageUrl: string
  /** The threshold tried; null: the configured one (RECIPE_DETECT_CONFIG.threshold, result.threshold). */
  threshold: number | null
  onThresholdChange: (value: number | null) => void
}) {
  const [active, setActive] = useState<number | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const ids = { slider: useId(), hint: useId() }
  const { width, height, regions } = result
  const current = threshold ?? result.threshold
  const tuned = threshold !== null && threshold !== result.threshold
  const names = useMemo(() => dishNames(regions, current), [regions, current])
  const closest = useMemo(() => regions.map(region => region.candidates[0]), [regions])
  const kept = useMemo(() => keptRows(closest, current), [closest, current])
  const stateOf = (index: number) => (active === null ? '' : active === index ? 'is-active' : 'is-dim')

  function tryThreshold(value: number) {
    onThresholdChange(value === result.threshold ? null : value)
  }

  return <div className="mt-view">
    <dl className="mt-summary">
      <div><dt>识别出的菜名</dt><dd>{names.length ? names.join('、') : '没有'}</dd></div>
      <div><dt>检测框</dt><dd title="业务搜图识别的框：边长小于 16 像素的丢掉，再按置信度取前 max_regions 个">
        {regions.length}{result.detected !== regions.length && <small className="rt-detected">（检测到 {result.detected} 个）</small>}</dd></div>
      <div><dt>caption</dt><dd><code>{result.caption}</code></dd></div>
      <div><dt>box_threshold</dt><dd>{result.boxThreshold}</dd></div>
      <div><dt>text_threshold</dt><dd>{result.textThreshold}</dd></div>
      <div><dt>max_regions</dt><dd>{result.maxRegions}</dd></div>
      <div><dt>图片</dt><dd>{width} × {height}</dd></div>
      <div><dt>耗时</dt><dd title={`其中菜谱推理 ${result.classifyMs.toFixed(0)} ms（${regions.length} 个框和整张图）`}>{(result.latencyMs / 1000).toFixed(2)} s</dd></div>
    </dl>

    <div className="mt-view-toolbar rt-threshold">
      <label className="mt-range" htmlFor={ids.slider}>菜谱阈值</label>
      <input id={ids.slider} className="rt-threshold-slider" type="range" min={0} max={1} step={STEP} value={current} aria-describedby={ids.hint}
        onChange={event => { setDraft(null); tryThreshold(Number(Number(event.target.value).toFixed(3))) }} />
      <input className="input rt-threshold-number" type="number" inputMode="decimal" min={0} max={1} step={STEP} aria-label="菜谱阈值的数值"
        value={draft ?? formatThreshold(current)} aria-invalid={draft !== null && typeof thresholdFrom(draft) !== 'number'}
        onChange={event => {
          setDraft(event.target.value)
          const value = thresholdFrom(event.target.value)
          if (typeof value === 'number') tryThreshold(value)
        }}
        onBlur={() => setDraft(null)} />
      {tuned
        ? <button type="button" className="text-button rt-reset" onClick={() => { setDraft(null); onThresholdChange(null) }}><RotateCcw size={13} aria-hidden />恢复配置值 {formatThreshold(result.threshold)}</button>
        : <span className="rt-threshold-note">配置值（RECIPE_DETECT_CONFIG.threshold）</span>}
    </div>
    <small id={ids.hint} className="mt-hint rt-threshold-hint">裁剪图和底库菜品的余弦相似度高于阈值（不含等于）才算识别出这道菜。拖动阈值只在浏览器里重新判断，不用重新调用；调好后改 RECIPE_DETECT_CONFIG.threshold（Nacos 里改了不用重启）。</small>

    <Outcome result={result} threshold={current} names={names} />

    <ImageStage width={width} height={height} imageUrl={imageUrl} alt="识别的图片">
      <svg className="mt-layer mt-boxes rt-boxes" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
        {regions.map((region, index) => {
          const [x1, y1, x2, y2] = region.box
          const accepted = isRecognised(closest[index], current)
          return <rect key={index} className={`${stateOf(index)} ${accepted ? '' : 'is-rejected'}`} style={colour(accepted)}
            x={x1} y={y1} width={x2 - x1} height={y2 - y1} vectorEffect="non-scaling-stroke" />
        })}
      </svg>
      {regions.map((region, index) => {
        const [x1, y1] = region.box
        const dish = closest[index]
        const accepted = isRecognised(dish, current)
        // A box at the top edge has no room above it: its label goes inside
        return <span key={index} className={`mt-box-label ${y1 / height < 0.08 ? 'is-inside' : ''} ${stateOf(index)}`}
          style={{ ...colour(accepted), left: percent(x1, width), top: percent(y1, height) }}>
          #{index + 1} {dish ? `${dish.label} ${dish.score.toFixed(3)}` : '—'}
        </span>
      })}
    </ImageStage>

    {regions.length > 0 && <div className="rt-table-scroll">
      <table className="mt-table rt-table" aria-label="每个检测框的识别结果">
        <thead><tr><th>#</th><th>模型的输入</th><th>检测</th><th>最相近的菜</th><th>相似度</th><th>结果</th><th>其他候选</th></tr></thead>
        <tbody>{regions.map((region, index) => {
          const [x1, y1, x2, y2] = region.box
          const [dish, ...others] = region.candidates
          const accepted = isRecognised(dish, current)
          return <tr key={index} className={active === index ? 'is-active' : ''} onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)}>
            <td>{index + 1}</td>
            <td><ModelInput imageUrl={imageUrl} width={width} height={height} box={region.box} label={`#${index + 1} 检测框的裁剪图（模型的输入）`} /></td>
            <td>
              <span className="rt-detection"><span className="mt-swatch" style={colour(accepted)} />{region.label || NO_LABEL} <small>{region.score.toFixed(2)}</small></span>
              <span className="rt-box" title={`位置 ${x1}, ${y1}, ${x2}, ${y2}`}>{x2 - x1} × {y2 - y1} 像素</span>
            </td>
            <td className="rt-dish">{dish?.label || '—'}</td>
            <td>{dish ? <ScoreAgainst score={dish.score} threshold={current} /> : '—'}</td>
            <td>{accepted
              ? <span className="rt-result is-accepted"><CircleCheck size={13} aria-hidden />{kept.has(index) ? '会返回' : '同名，只算一次'}</span>
              : <span className="rt-result"><CircleSlash size={13} aria-hidden />{dish?.label ? '低于阈值' : '没有菜名'}</span>}</td>
            <td><span className="rt-others">{others.length ? others.map(other => <span key={other.label}>{other.label} {other.score.toFixed(3)}</span>) : '—'}</span></td>
          </tr>
        })}</tbody>
      </table>
    </div>}

    <section className="rt-whole" aria-label="整张图的识别结果">
      <h3><ScanSearch size={14} aria-hidden />整张图<small>只作对比：业务搜图只识别检测框，不识别整张图</small></h3>
      <div className="rt-whole-body">
        <ModelInput imageUrl={imageUrl} width={width} height={height} label="整张图（模型的输入）" />
        <ol className="rt-candidates">
          {result.wholeImage.map((candidate, index) => <li key={candidate.label}>
            <span className="rt-rank">{index + 1}</span><span className="rt-dish">{candidate.label}</span>
            <ScoreAgainst score={candidate.score} threshold={current} />
          </li>)}
        </ol>
      </div>
    </section>
  </div>
}
