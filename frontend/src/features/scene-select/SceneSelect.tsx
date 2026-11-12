import { useEffect, useState } from 'react'
import { Layers3, RotateCcw } from 'lucide-react'
import { DEFAULT_SCENE, toApiSceneCode } from '../../entities/scene'
import { useSceneSelection } from '../scene-catalog'
/** compact: the refresh is an icon button (for a row with the label beside the select). */
export type SceneSelectProps = { value: string; onChange: (scene: string) => void; disabled?: boolean; id?: string; label?: string; className?: string; variant?: 'search' | 'management'; compact?: boolean }
export function SceneSelect({ value, onChange, disabled = false, id = 'scene-select', label = '场景', className = '', variant = 'search', compact = false }: SceneSelectProps) {
  const c = useSceneSelection(value, onChange)
  const [notice, setNotice] = useState('')
  useEffect(() => { if (c.fallback) setNotice(`原场景已停用或不可用，已切换到默认场景「${c.defaultScene?.name}」`) }, [c.fallback, c.defaultScene?.name])
  return <div className={`scene-select ${className}`} data-variant={variant}>
    <label className="scene-select-label" htmlFor={id}><Layers3 size={14} aria-hidden /><span>{label}</span></label>
    <select id={id} className={variant === 'management' ? 'mg-select' : 'input scene-select-input'} aria-label={label} disabled={disabled || !c.valid} value={c.active.some(s => s.code === toApiSceneCode(value)) ? toApiSceneCode(value) : ''} onChange={e => { setNotice(''); onChange(e.target.value) }}>
      {!c.selectionValid && <option value="">{c.loading ? '正在加载场景…' : '请选择有效场景'}</option>}
      {c.active.map(s => <option key={s.id} value={s.code}>{s.name}（{s.code}）</option>)}
    </select>
    {compact
      ? <button type="button" className="scene-select-refresh" disabled={c.loading} aria-label="刷新场景" title="刷新场景" onClick={() => void c.refresh()}><RotateCcw size={14} /></button>
      : <button type="button" className="text-button" disabled={c.loading} onClick={() => void c.refresh()}>刷新场景</button>}
    {(c.error || c.configurationError) && <p className="mg-form-error" role="alert">{c.error ?? '当前默认场景不可用，已禁止提交。'}<button type="button" className="text-button" onClick={() => void c.refresh()}>重试</button></p>}
    {notice && <p className="mg-help" role="status">{notice}</p>}
  </div>
}
export { DEFAULT_SCENE }
