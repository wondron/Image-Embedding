import { foodStateLabel, sceneDisplayLabel, SCENE_SEEDS } from '../../entities/scene'
import type { LibraryImageItem } from '../../entities/image'

export type ImageLabelPanelProps = {
  item: LibraryImageItem
  /** When write API is ready, wire these. Until then show 待接入. */
  apiReady?: boolean
  onManualAdd?: (sceneCode: string) => void
  onManualRemove?: (sceneCode: string) => void
  onFoodStateChange?: (state: 'raw' | 'cooked' | 'frozen' | null) => void
}

/**
 * Annotation panel: scene membership (rule/manual) + food_state (R13 / R14).
 * Write ops stay disabled with 待接入 until contract API lands.
 */
export function ImageLabelPanel({
  item,
  apiReady = false,
  onManualAdd,
  onManualRemove,
  onFoodStateChange,
}: ImageLabelPanelProps) {
  const memberCodes = new Set(item.scenes)
  const membershipByCode = Object.fromEntries(
    item.sceneMemberships.map(m => [m.code, m]),
  )

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
                  onFoodStateChange?.(v === '' ? null : v as 'raw' | 'cooked' | 'frozen')
                }}
              >
                <option value="">未设</option>
                <option value="raw">生</option>
                <option value="cooked">熟</option>
                <option value="frozen">冷冻</option>
              </select>
            ) : (
              <>
                {foodStateLabel(item.foodState)}
                <small> · 编辑待接入</small>
              </>
            )}
          </dd>
        </div>
      </dl>

      <h4 className="image-label-heading">场景成员</h4>
      <ul className="image-label-scenes">
        {SCENE_SEEDS.map(seed => {
          const member = membershipByCode[seed.code]
          const isMember = memberCodes.has(seed.code)
          const origin = member?.origin
          return (
            <li key={seed.code} className={isMember ? 'is-member' : 'not-member'}>
              <span className="scene-chip">{sceneDisplayLabel(seed.code)} <code>{seed.code}</code></span>
              {isMember ? (
                <span className="origin-tag">
                  origin={origin ?? '—'}
                  {apiReady && origin === 'manual' ? (
                    <button type="button" className="text-button" onClick={() => onManualRemove?.(seed.code)}>
                      移除手动
                    </button>
                  ) : null}
                </span>
              ) : (
                <span className="origin-tag">
                  （未成员）
                  {apiReady ? (
                    <button type="button" className="text-button" onClick={() => onManualAdd?.(seed.code)}>
                      手动加入
                    </button>
                  ) : (
                    <small>待接入</small>
                  )}
                </span>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mg-help">去掉手动记录后恢复规则分配（契约 §2.9）。本轮写 API 未交时仅只读展示。</p>
    </div>
  )
}