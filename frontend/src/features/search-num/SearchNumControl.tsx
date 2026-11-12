import { ListOrdered } from 'lucide-react'
import { SEARCH_NUM_DEFAULT, SEARCH_NUM_MAX, SEARCH_NUM_MIN } from '../../entities/search-result'

/** A whole number in SEARCH_NUM_MIN..SEARCH_NUM_MAX; anything that is not a number → the default. */
export function clampSearchNum(value: number): number {
  if (!Number.isFinite(value)) return SEARCH_NUM_DEFAULT
  return Math.min(SEARCH_NUM_MAX, Math.max(SEARCH_NUM_MIN, Math.round(value)))
}

export type SearchNumControlProps = {
  value: number
  disabled?: boolean
  onChange: (value: number) => void
}

/** 现网 Global: how many results to ask FlyB for (`search_num`). */
export function SearchNumControl({ value, disabled = false, onChange }: SearchNumControlProps) {
  return (
    <div className="search-num-control" role="group" aria-label="返回数量"
      title="最多返回这么多条；FlyB 找到的少、或开启了相似度过滤时会更少">
      <div className="search-num-header">
        <ListOrdered size={14} aria-hidden />
        <span className="search-num-title">返回数量</span>
        <span className="search-num-hint">仅现网 Global 生效 · 默认 {SEARCH_NUM_DEFAULT} · 范围 {SEARCH_NUM_MIN}–{SEARCH_NUM_MAX}</span>
      </div>
      <div className="search-num-row">
        <input
          type="range"
          className="search-num-slider"
          min={SEARCH_NUM_MIN}
          max={SEARCH_NUM_MAX}
          step={1}
          value={value}
          disabled={disabled}
          aria-label="返回数量滑块"
          onChange={event => onChange(clampSearchNum(Number(event.target.value)))}
        />
        <input
          type="number"
          className="input search-num-number"
          min={SEARCH_NUM_MIN}
          max={SEARCH_NUM_MAX}
          step={1}
          value={value}
          disabled={disabled}
          aria-label="返回数量数值"
          onChange={event => {
            const raw = event.target.value
            if (raw.trim() === '') return
            const parsed = Number(raw)
            if (!Number.isFinite(parsed)) return
            onChange(clampSearchNum(parsed))
          }}
        />
      </div>
    </div>
  )
}

export { SEARCH_NUM_DEFAULT }
