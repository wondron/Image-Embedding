import { foodStateLabel } from '../../entities/scene'

export type HeatingFieldsProps = {
  /** Show heating block (oven / outputs_heating). Hide entirely for general. */
  show: boolean
  heatingGroup?: string | null
  foodState?: string | null
  compact?: boolean
}

/**
 * Oven / outputs_heating display slots.
 * Missing fields show 「—」 so layout stays stable (R6 / §6.4).
 */
export function HeatingFields({ show, heatingGroup, foodState, compact = false }: HeatingFieldsProps) {
  if (!show) return null
  if (compact) {
    return (
      <span className="heating-fields-compact" title="加热信息">
        加热组 {heatingGroup?.trim() || '—'} · 状态 {foodStateLabel(foodState)}
      </span>
    )
  }
  return (
    <div className="heating-fields">
      <div><dt>加热组</dt><dd>{heatingGroup?.trim() || '—'}</dd></div>
      <div><dt>食物状态</dt><dd>{foodStateLabel(foodState)}</dd></div>
    </div>
  )
}