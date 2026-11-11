import { useId, useState } from 'react'
import { enumOptions, enumValueText, useEnumCatalog } from '../../entities/enum'
import {
  LICENSE_ENUM,
  MAX_DESCRIPTION_LENGTH,
  SOURCE_TYPE_ENUM,
  charLength,
  foodFieldsFilled,
} from '../../entities/image'
import type { Scene } from '../../entities/scene'
import { FoodFields, TagInput } from '../annotation-fields'
import { CategoryPicker, type CategoryTreeState } from '../category-picker'
import type { AnnotationErrors, ImportAnnotation } from './annotation'
import { effectiveOverrides, planScenes, toggleSceneOverride } from './scenePlan'
import { SceneAssignment } from './SceneAssignment'
import './index-upload.css'

export interface SceneCatalogView {
  /** Active scenes with their rules (GET /api/scenes). */
  active: readonly Scene[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export interface AnnotationFormProps {
  value: ImportAnnotation
  onChange: (next: ImportAnnotation) => void
  errors: AnnotationErrors
  categories: CategoryTreeState
  scenes: SceneCatalogView
  disabled?: boolean
}

/**
 * Every field an upload can carry: category (from the category tree) and label status, scenes
 * (rule preview + manual changes), tags, description, the food annotation, source and license.
 * Enum options come from GET /api/enums. Used for the batch and for one image alike.
 */
export function AnnotationForm({ value, onChange, errors, categories, scenes, disabled = false }: AnnotationFormProps) {
  const base = useId()
  const { catalog } = useEnumCatalog()
  const [foodOpen, setFoodOpen] = useState(() => foodFieldsFilled(value) > 0)
  const rows = planScenes(scenes.active, value.categoryId, categories.tree, value.sceneOverrides)
  const set = (patch: Partial<ImportAnnotation>) => onChange({ ...value, ...patch })
  const setCategory = (categoryId: number | null) => onChange({
    ...value,
    categoryId,
    // a new category may already give what was picked by hand
    sceneOverrides: effectiveOverrides(scenes.active, categoryId, categories.tree, value.sceneOverrides),
  })
  const descriptionLength = charLength(value.description.trim())
  const foodFilled = foodFieldsFilled(value)

  return <div className="ia-form">
    <section className="ia-section" aria-labelledby={`${base}-category-title`}>
      <h3 id={`${base}-category-title`}>类别与场景</h3>
      <CategoryPicker
        id={`${base}-category`}
        label="类别"
        tree={categories.tree}
        loading={categories.loading}
        loadError={categories.error}
        onRetry={categories.refresh}
        value={value.categoryId}
        onChange={setCategory}
        disabled={disabled}
        error={errors.category}
        hint={value.categoryId === null ? '不选则以「未标注」入库，之后可在图库里补标。' : undefined}
      />
      {value.categoryId !== null && <fieldset className="ia-choice" disabled={disabled}>
        <legend>标注状态</legend>
        <label><input type="radio" name={`${base}-label`} checked={value.labelStatus === 'auto'} onChange={() => set({ labelStatus: 'auto' })} />自动标注</label>
        <label><input type="radio" name={`${base}-label`} checked={value.labelStatus === 'verified'} onChange={() => set({ labelStatus: 'verified' })} />已核验</label>
        <small>批量指定的类别默认记为自动标注；逐张核对过再选「已核验」。</small>
      </fieldset>}
      <SceneAssignment
        legend="场景"
        rows={rows}
        onToggle={row => set({ sceneOverrides: toggleSceneOverride(value.sceneOverrides, row) })}
        disabled={disabled}
        loading={scenes.loading}
        error={scenes.error}
        onRetry={scenes.refresh}
        hasCategory={value.categoryId !== null}
      />
    </section>

    <section className="ia-section" aria-labelledby={`${base}-text-title`}>
      <h3 id={`${base}-text-title`}>标签与描述</h3>
      <div className="ia-field">
        <label htmlFor={`${base}-tags`}>标签</label>
        <TagInput id={`${base}-tags`} tags={value.tags} onChange={tags => set({ tags })} disabled={disabled} error={errors.tags} describedBy={errors.tags ? `${base}-tags-error` : undefined} />
        {errors.tags && <small className="ia-field-error" id={`${base}-tags-error`} role="alert">{errors.tags}</small>}
      </div>
      <div className="ia-field">
        <label htmlFor={`${base}-description`}>描述</label>
        <textarea
          id={`${base}-description`}
          rows={3}
          disabled={disabled}
          placeholder="例如：整块牛排，五分熟，带骨"
          value={value.description}
          aria-invalid={errors.description ? true : undefined}
          onChange={event => set({ description: event.target.value })}
        />
        <small className={`ia-counter${errors.description ? ' is-over' : ''}`}>{errors.description ?? `${descriptionLength} / ${MAX_DESCRIPTION_LENGTH}`}</small>
      </div>
    </section>

    <details className="ia-section ia-collapsible" open={foodOpen} onToggle={event => setFoodOpen(event.currentTarget.open)}>
      <summary><h3>食物标注</h3><small>{foodFilled ? `已填 ${foodFilled} 项` : '可选'}</small></summary>
      <FoodFields idBase={base} value={value} onChange={set} errors={errors} disabled={disabled} />
      <p className="ia-muted">只有食物状态参与检索过滤；其余用于展示和后续加热计算，都可留空。</p>
    </details>

    <details className="ia-section ia-collapsible">
      <summary><h3>来源与许可</h3><small>{enumValueText(catalog, SOURCE_TYPE_ENUM, value.sourceType)} · {enumValueText(catalog, LICENSE_ENUM, value.license)}</small></summary>
      <div className="ia-grid">
        <div className="ia-field">
          <label htmlFor={`${base}-source`}>来源</label>
          <select id={`${base}-source`} disabled={disabled} value={value.sourceType} onChange={event => set({ sourceType: event.target.value })}>
            {enumOptions(catalog, SOURCE_TYPE_ENUM, value.sourceType).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
        <div className="ia-field">
          <label htmlFor={`${base}-license`}>许可</label>
          <select id={`${base}-license`} disabled={disabled} value={value.license} onChange={event => set({ license: event.target.value })}>
            {enumOptions(catalog, LICENSE_ENUM, value.license).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      </div>
      <p className="ia-muted">确认不能使用的数据可以按许可整批剔除。</p>
    </details>
  </div>
}
