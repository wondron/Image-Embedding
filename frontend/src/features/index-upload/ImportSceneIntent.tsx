import { useEffect, useState } from 'react'
import { SceneSelect } from '../scene-select'
import { useSceneCatalog } from '../scene-catalog'
import { toApiSceneCode } from '../../entities/scene'
export type ImportSceneIntentProps = { scenes: string[]; onChange: (scenes: string[]) => void; disabled?: boolean }
export function ImportSceneIntent({ scenes, onChange, disabled }: ImportSceneIntentProps) {
  const c = useSceneCatalog(); const [notice, setNotice] = useState('')
  useEffect(() => {
    if (!c.valid) return
    const next = [...new Set(scenes.map(v => toApiSceneCode(v, '')).filter(code => c.active.some(s => s.code === code)))]
    if (!next.length) next.push(c.defaultScene!.code)
    if (JSON.stringify(next) !== JSON.stringify(scenes)) { onChange(next); setNotice('已移除不可用场景。请复核后再提交') }
  }, [c.valid, c.revision, scenes, onChange])
  return <div className="import-scene-intent">
    <SceneSelect id="import-scene-intent" label="场景意向" variant="management" value={scenes[0] ?? ''} disabled={disabled} onChange={code => onChange([code, ...scenes.filter(s => s !== code)])} />
    <fieldset disabled={disabled || !c.valid}><legend>多选场景意向</legend>{c.active.map(s => <label key={s.id}><input type="checkbox" checked={scenes.includes(s.code)} onChange={e => onChange(e.target.checked ? [...scenes, s.code] : scenes.filter(v => v !== s.code))} />{s.name}</label>)}</fieldset>
    {notice && <p role="status" className="mg-help">{notice}</p>}
    <p className="mg-help">此处仅为入库意向，不是人工加入。最终场景成员以规则分配与人工覆盖为准。</p>
  </div>
}
