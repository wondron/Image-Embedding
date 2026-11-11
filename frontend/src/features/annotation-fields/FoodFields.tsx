import { enumOptions, useEnumCatalog } from '../../entities/enum'
import {
  AMOUNT_FIELDS,
  FOOD_PROCESSING_ENUM,
  FOOD_STATE_ENUM,
  type FoodFieldValues,
  type ImageFieldErrors,
} from '../../entities/image'
import './annotation-fields.css'

/**
 * The six food fields in a two-column grid: state and processing from GET /api/enums (a stored
 * value the catalog lacks is still offered), the amounts as typed with their checks
 * (entities/image/fields). onChange gets the changed field only.
 */
export function FoodFields({ idBase, value, onChange, errors, disabled = false }: {
  idBase: string
  value: FoodFieldValues
  onChange: (patch: Partial<FoodFieldValues>) => void
  errors: ImageFieldErrors
  disabled?: boolean
}) {
  const { catalog } = useEnumCatalog()
  return <div className="ia-grid">
    <div className="ia-field">
      <label htmlFor={`${idBase}-food-state`}>食物状态</label>
      <select id={`${idBase}-food-state`} disabled={disabled} value={value.foodState} onChange={event => onChange({ foodState: event.target.value })}>
        <option value="">未填写</option>
        {enumOptions(catalog, FOOD_STATE_ENUM, value.foodState).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </div>
    <div className="ia-field">
      <label htmlFor={`${idBase}-food-processing`}>加工状态</label>
      <select id={`${idBase}-food-processing`} disabled={disabled} value={value.foodProcessing} onChange={event => onChange({ foodProcessing: event.target.value })}>
        <option value="">未填写</option>
        {enumOptions(catalog, FOOD_PROCESSING_ENUM, value.foodProcessing).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </div>
    {AMOUNT_FIELDS.map(field => {
      const id = `${idBase}-${field.key}`
      const problem = errors[field.key]
      return <div className="ia-field" key={field.key}>
        <label htmlFor={id}>{field.label}</label>
        <div className={`ia-unit${problem ? ' is-invalid' : ''}`}>
          <input
            id={id}
            inputMode="numeric"
            disabled={disabled}
            placeholder={field.min === 0 ? '≥ 0' : '≥ 1'}
            value={value[field.key]}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${id}-error` : undefined}
            onChange={event => onChange({ [field.key]: event.target.value })}
          />
          {field.unit && <span aria-hidden="true">{field.unit}</span>}
        </div>
        {problem && <small className="ia-field-error" id={`${id}-error`} role="alert">{problem}</small>}
      </div>
    })}
  </div>
}
