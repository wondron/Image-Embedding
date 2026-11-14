import { ListOrdered } from 'lucide-react'
import { SEARCH_NUM_DEFAULT, SEARCH_NUM_MAX, SEARCH_NUM_MIN } from '../../entities/search-result'

/** The counts a control accepts, and what a non-number becomes. */
export interface CountRange {
  min: number
  max: number
  fallback: number
}

/** 现网 Global `search_num`. */
const GLOBAL_RANGE: CountRange = { min: SEARCH_NUM_MIN, max: SEARCH_NUM_MAX, fallback: SEARCH_NUM_DEFAULT }

/** A whole number in range.min..range.max; anything that is not a number → range.fallback. */
export function clampCount(value: number, range: CountRange): number {
  if (!Number.isFinite(value)) return range.fallback
  return Math.min(range.max, Math.max(range.min, Math.round(value)))
}

/** A whole number in SEARCH_NUM_MIN..SEARCH_NUM_MAX; anything that is not a number → the default. */
export function clampSearchNum(value: number): number {
  return clampCount(value, GLOBAL_RANGE)
}

export type SearchNumControlProps = {
  value: number
  disabled?: boolean
  onChange: (value: number) => void
  /** Default: 现网 Global `search_num` (1–20, default 20). */
  range?: CountRange
  /** The line above the control; default: what it means for 现网 Global. */
  hint?: string
  /** Tooltip of the control. */
  title?: string
}

/** How many results to ask for: 现网 Global `search_num` by default, or another count with its own range (业务搜图 `top_k`). */
export function SearchNumControl({ value, disabled = false, onChange, range = GLOBAL_RANGE, hint, title }: SearchNumControlProps) {
  return (
    <div className="search-num-control" role="group" aria-label="返回数量"
      title={title ?? '最多返回这么多条；FlyB 找到的少、或开启了相似度过滤时会更少'}>
      <span className="search-num-hint">{hint ?? `仅现网 Global 生效 · 默认 ${range.fallback} · 范围 ${range.min}–${range.max}`}</span>
      <div className="search-num-header">
        <ListOrdered size={14} aria-hidden />
        <span className="search-num-title">返回数量</span>
      </div>
      <div className="search-num-row">
        <input
          type="range"
          className="search-num-slider"
          min={range.min}
          max={range.max}
          step={1}
          value={value}
          disabled={disabled}
          aria-label="返回数量滑块"
          onChange={event => onChange(clampCount(Number(event.target.value), range))}
        />
        <input
          type="number"
          className="input search-num-number"
          min={range.min}
          max={range.max}
          step={1}
          value={value}
          disabled={disabled}
          aria-label="返回数量数值"
          onChange={event => {
            const raw = event.target.value
            if (raw.trim() === '') return
            const parsed = Number(raw)
            if (!Number.isFinite(parsed)) return
            onChange(clampCount(parsed, range))
          }}
        />
      </div>
    </div>
  )
}

export { SEARCH_NUM_DEFAULT }
