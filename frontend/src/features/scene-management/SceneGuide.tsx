import { Filter, Images, ScanSearch, Tag } from 'lucide-react'
import type { Scene } from '../../entities/scene'
import { countText } from './summary'

/**
 * What the page shows before a scene is picked: how a scene takes part in a search (the order of
 * the four steps is the order things happen in), and which changes need the full sync.
 */
export function SceneGuide({ scenes, defaultScene, libraryTotal }: {
  scenes: readonly Scene[] | null
  defaultScene: string | null
  libraryTotal: number | null
}) {
  const active = scenes?.filter(scene => scene.isActive).length ?? 0
  const defaultName = scenes?.find(scene => scene.code === defaultScene)?.name ?? null
  return <div className="scene-guide">
    <h2>场景怎么起作用</h2>
    <ol className="scene-flow">
      <li>
        <span className="scene-flow-icon"><Images size={18} aria-hidden /></span>
        <strong>图库</strong>
        <p>全部已入库的图片{libraryTotal !== null ? `，现在 ${countText(libraryTotal)} 张` : ''}。</p>
      </li>
      <li>
        <span className="scene-flow-icon"><Filter size={18} aria-hidden /></span>
        <strong>收录</strong>
        <p>每个场景按类别或菜系规则收录其中一部分；全包含的场景收录全部。</p>
      </li>
      <li>
        <span className="scene-flow-icon"><Tag size={18} aria-hidden /></span>
        <strong>对应标签</strong>
        <p>每个场景有自己的一套场景标签，规则把类别对应到它们。</p>
      </li>
      <li>
        <span className="scene-flow-icon"><ScanSearch size={18} aria-hidden /></span>
        <strong>搜图</strong>
        <p>搜图时选哪个场景，就只在它收录的图片里找，结果显示它的标签。</p>
      </li>
    </ol>
    <div className="scene-effects">
      <div>
        <h3>改了立即生效</h3>
        <p>场景的名称和说明、场景标签、规则对应的标签、输出加热信息。</p>
      </div>
      <div className="is-sync">
        <h3>改了要全量同步</h3>
        <p>增删收录规则或改规则的类别、菜系，启用或停用场景，开关全包含。同步前，已入库的图片和搜图结果仍按旧配置。</p>
      </div>
    </div>
    {scenes && scenes.length > 0 && <p className="scene-guide-hint">
      现在有 {scenes.length} 个场景，{active} 个启用{defaultName ? `；没有指定场景的搜图请求用默认场景「${defaultName}」` : ''}。从左侧选一个场景，查看它的收录规则和场景标签。
    </p>}
  </div>
}
