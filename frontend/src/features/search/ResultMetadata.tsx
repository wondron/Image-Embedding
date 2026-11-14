import type { SearchImage } from '../../entities/search-result'

export function ResultSceneLabel({ value }: { value: string | null }) {
  return value?.trim()
    ? <span className="result-scene-label" title={value}>{value}</span>
    : <span className="result-field-empty">未映射</span>
}

export function ResultTags({ value }: { value: string }) {
  return value.trim()
    ? <span className="result-tags-text" title={value}>{value}</span>
    : <span className="result-field-empty">无标签</span>
}

/** Keep the stored category, scene mapping and tags distinct, even when their values match. */
export function ResultMetadata({ item, onDetail }: { item: SearchImage; onDetail: () => void }) {
  return <dl className="result-labels">
    <div className="result-category-row">
      <dt>类别</dt>
      <dd><h3><button onClick={onDetail} title={item.category ?? '未分类'}>{item.category ?? '未分类'}</button></h3></dd>
    </div>
    <div>
      <dt>场景标签</dt>
      <dd><ResultSceneLabel value={item.sceneLabel} /></dd>
    </div>
    <div className="result-tags-row">
      <dt>标签</dt>
      <dd><ResultTags value={item.tags} /></dd>
    </div>
  </dl>
}
