import { Layers3 } from 'lucide-react'
import { DEFAULT_SCENE, SCENE_SEEDS, toApiSceneCode } from '../../entities/scene'

export type SceneSelectProps = {
  value: string
  onChange: (scene: string) => void
  disabled?: boolean
  id?: string
  /** Accessible name */
  label?: string
  className?: string
  /** Use management-view select styling */
  variant?: 'search' | 'management'
}

/**
 * Shared scene selector for 搜图 + 图库 (R21 / A3 / V5).
 * Default general. Changing value does not auto-search — caller decides.
 */
export function SceneSelect({
  value,
  onChange,
  disabled = false,
  id = 'scene-select',
  label = '场景',
  className = '',
  variant = 'search',
}: SceneSelectProps) {
  const apiValue = toApiSceneCode(value, DEFAULT_SCENE)
  const selectClass = variant === 'management' ? 'mg-select' : 'input scene-select-input'

  return (
    <div className={`scene-select ${className}`.trim()} data-variant={variant}>
      <label className="scene-select-label" htmlFor={id}>
        <Layers3 size={14} aria-hidden />
        <span>{label}</span>
      </label>
      <select
        id={id}
        className={selectClass}
        aria-label={label}
        disabled={disabled}
        value={apiValue}
        onChange={event => onChange(toApiSceneCode(event.target.value, DEFAULT_SCENE))}
      >
        {SCENE_SEEDS.map(seed => (
          <option key={seed.code} value={seed.code}>
            {seed.label}（{seed.code}）{seed.hint ? ` · ${seed.hint}` : ''}
          </option>
        ))}
      </select>
    </div>
  )
}

export { DEFAULT_SCENE }