import type { FoodState, HeatingGroup } from '../../entities/image'
import { foodStateLabel } from '../../entities/scene'

export type HeatingFieldsProps = {
  /** Show heating block (oven / outputs_heating). Hide entirely for general. */
  show: boolean
  /** Wire `heating_group` object; the display uses its name. */
  heatingGroup?: HeatingGroup | null
  foodState?: FoodState | null
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
        加热组 {heatingGroup?.name ?? '—'} · 状态 {foodStateLabel(foodState)}
      </span>
    )
  }
  return (
    <div className="heating-fields">
      <div><dt>加热组</dt><dd>{heatingGroup?.name ?? '—'}</dd></div>
      <div><dt>食物状态</dt><dd>{foodStateLabel(foodState)}</dd></div>
    </div>
  )
}