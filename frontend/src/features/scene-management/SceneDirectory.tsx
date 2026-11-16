import { Layers3, LoaderCircle, Plus } from 'lucide-react'
import type { Scene } from '../../entities/scene'
import { EmptyState } from '../../shared/ui'
import { countText, meterWidth, sceneCoverage, scopeText } from './summary'

const LOADING_TEXT = '正在加载场景目录…'

/**
 * The scenes, one row each: its name and code, how many images it holds and which part of the
 * library that is (the bar), so the list already says that a scene is a subset of the library.
 */
export function SceneDirectory({ scenes, defaultScene, libraryTotal, selectedId, loading, canWrite, onSelect, onCreate }: {
  /** null: the catalog has not been read yet. */
  scenes: readonly Scene[] | null
  defaultScene: string | null
  /** Images in the library; null when unknown (no bar then). */
  libraryTotal: number | null
  selectedId: number | null
  loading: boolean
  canWrite: boolean
  onSelect: (id: number) => void
  onCreate: () => void
}) {
  return <aside className="mg-panel scene-list" aria-label="场景列表">
    <div className="scene-list-head">
      <h2>
        场景{scenes && <span className="scene-list-total">{scenes.length}</span>}
        {/* A refresh keeps the rows it already has: only a small mark says it is reading again */}
        {loading && scenes && <span className="scene-list-loading" role="status"><LoaderCircle size={14} className="spin" aria-hidden /><span className="sr-only">{LOADING_TEXT}</span></span>}
      </h2>
      <button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={onCreate}><Plus size={15} />新建场景</button>
    </div>
    {scenes && scenes.length > 0 && <ul className="scene-list-items">
      {scenes.map(scene => {
        const coverage = sceneCoverage(scene, libraryTotal)
        const selected = scene.id === selectedId
        return <li key={scene.id}>
          <button type="button" className={`scene-list-item${selected ? ' is-selected' : ''}${scene.isActive ? '' : ' is-off'}`} aria-pressed={selected} onClick={() => onSelect(scene.id)}>
            <span className="scene-list-name">
              <strong>{scene.name}</strong>
              {scene.code === defaultScene && <span className="scene-tag is-default">默认</span>}
              {!scene.isActive && <span className="scene-tag is-off">已停用</span>}
            </span>
            <span className="scene-list-count">{countText(coverage.members)}<small>张</small></span>
            <code className="scene-list-code">{scene.code}</code>
            <span className="scene-list-scope">
              {scene.isActive && coverage.percent !== null && <span className="scene-meter" aria-hidden="true"><span style={{ width: `${meterWidth(coverage)}%` }} /></span>}
              <span>{scopeText(scene)}{scene.isActive && !scene.includeAll && coverage.percentText ? `，占图库 ${coverage.percentText}` : ''}</span>
            </span>
          </button>
        </li>
      })}
    </ul>}
    {scenes && scenes.length === 0 && <EmptyState icon={Layers3} title="还没有场景" description="新建一个场景，再给它添加收录规则。" />}
    {!scenes && (loading
      ? <p className="scene-list-blank" role="status"><LoaderCircle size={15} className="spin" aria-hidden />{LOADING_TEXT}</p>
      : <p className="scene-list-blank">场景目录没有读到，点「刷新」再试一次。</p>)}
  </aside>
}
