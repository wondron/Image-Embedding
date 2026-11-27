import type { ReactNode } from 'react'
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

/** A result whose annotation was changed after the search (its card shows the saved values). */
export function ResultEditedMark() {
  return <span className="result-edited" title="搜索之后修改过标注，这里显示的是保存后的内容；相似度仍是搜索时的分数">已修改</span>
}

/**
 * A hit of the self-built library carries the library's own annotation (category, the scene's
 * label, tags); a result of the global search only has a title and the site it came from.
 */
export function isLibraryHit(item: Pick<SearchImage, 'source'>): boolean {
  return item.source === 'self'
}

export interface ResultMetadataProps {
  item: SearchImage
  onDetail: () => void
  /** Sits at the end of the first row (the download button). */
  action?: ReactNode
  /** A last row under a rule (the heating information of scenes that output it). */
  footer?: ReactNode
  /** Its annotation was changed after the search. */
  edited?: boolean
}

/**
 * What a result card says under its image, as captioned rows so the stored category, the scene's
 * label and the tags stay apart even when their values match. The first row is the card's heading
 * and carries the action; rows that do not apply to a channel are left out, not shown empty.
 * 菜谱识别's card (业务搜图) is headed by the dish names it recognised in the query image.
 */
export function ResultMetadata({ item, onDetail, action, footer, edited = false }: ResultMetadataProps) {
  const library = isLibraryHit(item)
  const recipe = item.source === 'recipe'
  const heading = library ? item.category ?? '未分类' : item.title
  return <>
    <dl className="result-labels">
      <div className="result-heading-row">
        <dt>{library ? '类别' : recipe ? '菜名' : '标题'}</dt>
        <dd>
          <h3><button type="button" className={library && !item.category ? 'is-empty' : ''} onClick={onDetail} title={heading}>{heading}</button></h3>
          {edited && <ResultEditedMark />}
          {action}
        </dd>
      </div>
      {library
        ? <>
          <div>
            <dt>场景标签</dt>
            <dd><ResultSceneLabel value={item.sceneLabel} /></dd>
          </div>
          <div className="result-tags-row">
            <dt>标签</dt>
            <dd><ResultTags value={item.tags} /></dd>
          </div>
        </>
        : <div>
          <dt>来源</dt>
          <dd>{recipe
            ? <span className="result-site" title="菜谱识别在查询图里识别出的菜名，不是图库里的图片">查询图里识别出的菜</span>
            : item.siteName?.trim()
              ? <span className="result-site" title={item.siteName}>{item.siteName}</span>
              : <span className="result-field-empty">未提供</span>}</dd>
        </div>}
    </dl>
    {footer && <div className="result-card-meta">{footer}</div>}
  </>
}
