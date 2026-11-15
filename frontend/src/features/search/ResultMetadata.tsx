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
}

/**
 * What a result card says under its image, as captioned rows so the stored category, the scene's
 * label and the tags stay apart even when their values match. The first row is the card's heading
 * and carries the action; rows that do not apply to a channel are left out, not shown empty.
 */
export function ResultMetadata({ item, onDetail, action, footer }: ResultMetadataProps) {
  const library = isLibraryHit(item)
  const heading = library ? item.category ?? '未分类' : item.title
  return <>
    <dl className="result-labels">
      <div className="result-heading-row">
        <dt>{library ? '类别' : '标题'}</dt>
        <dd>
          <h3><button type="button" className={library && !item.category ? 'is-empty' : ''} onClick={onDetail} title={heading}>{heading}</button></h3>
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
          <dd>{item.siteName?.trim()
            ? <span className="result-site" title={item.siteName}>{item.siteName}</span>
            : <span className="result-field-empty">未提供</span>}</dd>
        </div>}
    </dl>
    {footer && <div className="result-card-meta">{footer}</div>}
  </>
}
