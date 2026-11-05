import { SceneSelect } from '../scene-select'
import { DEFAULT_SCENE } from '../../entities/scene'

export type ImportSceneIntentProps = {
  scenes: string[]
  onChange: (scenes: string[]) => void
  disabled?: boolean
}

/**
 * Import-side scene intent (R11). Default general.
 * Final membership = rules + manual; UI explains that.
 * Multi-select: primary select + chips for extras (API field TBD).
 */
export function ImportSceneIntent({ scenes, onChange, disabled }: ImportSceneIntentProps) {
  const primary = scenes[0] ?? DEFAULT_SCENE

  return (
    <div className="import-scene-intent">
      <SceneSelect
        id="import-scene-intent"
        label="场景意向"
        variant="management"
        value={primary}
        disabled={disabled}
        onChange={code => {
          const rest = scenes.filter(s => s !== code)
          onChange([code, ...rest])
        }}
      />
      <p className="mg-help">
        默认「通用」。最终场景成员以规则分配 + 手动覆盖为准；此处仅为入库意向（字段待契约）。
      </p>
    </div>
  )
}