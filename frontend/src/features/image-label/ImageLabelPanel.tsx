import { enumOptions, enumValueText, useEnumCatalog } from '../../entities/enum'
import { FOOD_STATE_ENUM, type FoodState, type LibraryImageItem } from '../../entities/image'
import { SceneMembershipPanel } from '../scene-memberships/SceneMembershipPanel'

export type ImageLabelPanelProps = {
  item: LibraryImageItem
  apiBase?: string
  /** After a scene membership write (e.g. reload the library page). */
  onSaved?: () => void
  /** Open the scene management sync tab. */
  onManage?: () => void
  /** Food state editing: off until its write API lands (shows 编辑待接入). */
  apiReady?: boolean
  onFoodStateChange?: (state: FoodState | null) => void
}

/**
 * Annotation panel: scene membership (SceneMembershipPanel: rule / manual, saved and pushed to
 * the search index per click) + food_state (R13 / R14, read-only until its write API lands).
 * Food state labels and options come from GET /api/enums/food_state.
 */
export function ImageLabelPanel({
  item,
  apiReady = false,
  apiBase, onSaved, onManage,
  onFoodStateChange,
}: ImageLabelPanelProps) {
  const { catalog } = useEnumCatalog()

  return (
    <div className="image-label-panel">
      <dl className="detail-fields">
        <div>
          <dt>图片 ID</dt>
          <dd className="monospace">{item.id}</dd>
        </div>
        <div>
          <dt>通用类别</dt>
          <dd>{item.category ?? '—'} <small>（待契约 API）</small></dd>
        </div>
        <div>
          <dt>食物状态</dt>
          <dd>
            {apiReady ? (
              <select
                className="mg-select"
                aria-label="食物状态"
                value={item.foodState ?? ''}
                onChange={event => {
                  const v = event.target.value
                  onFoodStateChange?.(v === '' ? null : v)
                }}
              >
                <option value="">未设</option>
                {enumOptions(catalog, FOOD_STATE_ENUM, item.foodState).map(option => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            ) : (
              <>
                {enumValueText(catalog, FOOD_STATE_ENUM, item.foodState)}
                <small> · 编辑待接入</small>
              </>
            )}
          </dd>
        </div>
      </dl>

      <SceneMembershipPanel imageId={item.id} apiBase={apiBase} onSaved={onSaved} onManage={onManage} />
    </div>
  )
}