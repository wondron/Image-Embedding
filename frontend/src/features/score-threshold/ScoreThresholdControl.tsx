import { SlidersHorizontal } from 'lucide-react'
import {
  SCORE_DEFAULT, SCORE_MAX, SCORE_MIN, SCORE_STEP,
  clampScoreThreshold, formatScore,
} from '../../shared/lib/score'

export type ScoreThresholdControlProps = {
  scoreThreshold: number
  rejectOff: boolean
  disabled?: boolean
  onScoreChange: (value: number) => void
  onRejectOffChange: (off: boolean, restoreScore?: number) => void
  scoreBeforeOff: number
}

/** Reject-threshold control — orthogonal to scene (K2 / C8). */
export function ScoreThresholdControl({
  scoreThreshold,
  rejectOff,
  disabled = false,
  onScoreChange,
  onRejectOffChange,
  scoreBeforeOff,
}: ScoreThresholdControlProps) {
  return (
    <div className="score-threshold-control" aria-label="拒识阈值">
      <div className="score-threshold-header">
        <SlidersHorizontal size={14} aria-hidden />
        <span className="score-threshold-title">拒识阈值</span>
        <span className="score-threshold-hint">仅自建库生效 · 默认 {SCORE_DEFAULT} · 范围 {SCORE_MIN}–{SCORE_MAX}</span>
      </div>
      <div className="score-threshold-row">
        <input
          type="range"
          className="score-threshold-slider"
          min={SCORE_MIN}
          max={SCORE_MAX}
          step={SCORE_STEP}
          value={scoreThreshold}
          disabled={disabled || rejectOff}
          aria-label="拒识阈值滑块"
          aria-valuemin={SCORE_MIN}
          aria-valuemax={SCORE_MAX}
          aria-valuenow={scoreThreshold}
          onChange={event => onScoreChange(clampScoreThreshold(Number(event.target.value)))}
        />
        <input
          type="number"
          className="input score-threshold-number"
          min={SCORE_MIN}
          max={SCORE_MAX}
          step={SCORE_STEP}
          value={formatScore(scoreThreshold)}
          disabled={disabled || rejectOff}
          aria-label="拒识阈值数值"
          onChange={event => {
            const raw = event.target.value
            if (raw.trim() === '') return
            const parsed = Number(raw)
            if (!Number.isFinite(parsed)) return
            onScoreChange(clampScoreThreshold(parsed))
          }}
        />
        <label className="score-threshold-off">
          <input
            type="checkbox"
            checked={rejectOff}
            disabled={disabled}
            onChange={event => {
              const off = event.target.checked
              if (off) onRejectOffChange(true, scoreThreshold)
              else onRejectOffChange(false, scoreBeforeOff)
            }}
          />
          关闭拒识（阈值=0）
        </label>
      </div>
    </div>
  )
}

export { SCORE_DEFAULT, clampScoreThreshold, formatScore }