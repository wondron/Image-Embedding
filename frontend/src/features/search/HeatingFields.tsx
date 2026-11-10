import { useEnumValueText } from '../../entities/enum'
import { FOOD_STATE_ENUM, type FoodState, type HeatingGroup } from '../../entities/image'

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
 * Missing fields show 「—」 so layout stays stable (R6 / §6.4). The food state label comes from
 * GET /api/enums/food_state; a value without a loaded label is shown as stored.
 */
export function HeatingFields({ show, heatingGroup, foodState, compact = false }: HeatingFieldsProps) {
  const foodStateText = useEnumValueText(FOOD_STATE_ENUM, foodState)
  if (!show) return null
  if (compact) {
    return (
      <span className="heating-fields-compact" title="加热信息">
        加热组 {heatingGroup?.name ?? '—'} · 状态 {foodStateText}
      </span>
    )
  }
  return (
    <div className="heating-fields">
      <div><dt>加热组</dt><dd>{heatingGroup?.name ?? '—'}</dd></div>
      <div><dt>食物状态</dt><dd>{foodStateText}</dd></div>
    </div>
  )
}
