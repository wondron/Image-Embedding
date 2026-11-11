import { RotateCcw } from 'lucide-react'
import type { ScenePlanRow } from './scenePlan'

function source(row: ScenePlanRow): { text: string; kind: 'all' | 'rule' | 'manual' | 'none' } {
  if (row.locked) return { text: '全部图片', kind: 'all' }
  if (row.override === 'include') return { text: '人工纳入', kind: 'manual' }
  if (row.override === 'exclude') return { text: '人工排除', kind: 'manual' }
  if (row.ruleMember) return { text: '按规则', kind: 'rule' }
  return { text: '不纳入', kind: 'none' }
}

/**
 * Which scenes the upload goes into: the rule result for the chosen category, with the person's
 * changes on top (scenePlan). A change is written as a manual include / exclude after the upload.
 */
export function SceneAssignment({ legend, rows, onToggle, disabled = false, loading = false, error, onRetry, hasCategory }: {
  legend: string
  rows: readonly ScenePlanRow[]
  onToggle: (row: ScenePlanRow) => void
  disabled?: boolean
  loading?: boolean
  error?: string | null
  onRetry?: () => void
  hasCategory: boolean
}) {
  const changed = rows.filter(row => row.override).length
  return <fieldset className="ia-scenes" disabled={disabled}>
    <legend>{legend}{changed > 0 && <span className="ia-badge">{changed} 项人工调整</span>}</legend>
    {error && <p className="ia-inline-error" role="alert">读取场景失败：{error}{onRetry && <> <button type="button" className="mg-text-button" onClick={onRetry}><RotateCcw size={13} />重试</button></>}</p>}
    {loading && !rows.length && <p className="ia-muted" role="status">正在读取场景…</p>}
    {rows.length > 0 && <ul className="ia-scene-list">
      {rows.map(row => {
        const from = source(row)
        return <li key={row.scene.id}>
          <label className={`ia-scene${row.member ? ' is-member' : ''}${row.locked ? ' is-locked' : ''}`}>
            <input type="checkbox" checked={row.member} disabled={row.locked} onChange={() => onToggle(row)} />
            <span className="ia-scene-name">{row.scene.name}<code>{row.scene.code}</code></span>
            <span className={`ia-scene-source is-${from.kind}`}>{from.text}</span>
          </label>
        </li>
      })}
    </ul>}
    <p className="ia-muted">{hasCategory
      ? '按所选类别和场景规则预览。和规则结果不同的勾选，会在入库后写成人工纳入 / 排除。'
      : '未设类别时只按全包含场景归属；其他场景可以手动勾选，入库后写成人工纳入。'}</p>
  </fieldset>
}
