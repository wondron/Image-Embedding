import type { ReactNode } from 'react'
import { ArrowRight, Images, Search } from 'lucide-react'
import { EMPTY_COPY } from '../../shared/config'

export function SearchNeverEmpty() {
  const copy = EMPTY_COPY.searchNever
  return (
    <div className="results-empty">
      <span className="empty-image-icon" aria-hidden="true"><Images size={34} strokeWidth={1.2} /></span>
      <h3>{copy.title}</h3>
      <p>{copy.description}</p>
      <div className="empty-steps">
        <span>上传图片</span><ArrowRight size={13} />
        <span>检索相似结果</span><ArrowRight size={13} />
        <span>查看与下载</span>
      </div>
    </div>
  )
}

export type SearchNoMatchProps = {
  /** When library is known empty (e.g. from prior list total=0), show import CTA */
  libraryLikelyEmpty?: boolean
  onGoImport?: () => void
  message?: string | null
  /** A further action next to the import CTA (业务搜图: look at the query in 模型工具). */
  action?: ReactNode
}

export function SearchNoMatch({ libraryLikelyEmpty, onGoImport, message, action }: SearchNoMatchProps) {
  const copy = EMPTY_COPY.searchNoMatch
  return (
    <div className="no-results">
      <Search size={35} strokeWidth={1.2} />
      <h3>{copy.title}</h3>
      <p>{message?.trim() || copy.description}</p>
      <div className="no-results-actions">
        {(libraryLikelyEmpty || onGoImport) && (
          <button type="button" className="btn btn-primary" onClick={onGoImport}>
            {copy.ctaImport}
          </button>
        )}
        {action}
      </div>
    </div>
  )
}

export function SearchFilteredEmpty({ onClear }: { onClear: () => void }) {
  const copy = EMPTY_COPY.searchFilteredEmpty
  return (
    <div className="no-results">
      <Search size={35} strokeWidth={1.2} />
      <h3>{copy.title}</h3>
      <p>{copy.description}</p>
      <button type="button" className="btn btn-secondary" onClick={onClear}>清除筛选</button>
    </div>
  )
}