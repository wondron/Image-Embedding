import { useEffect, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'

export interface LibraryPaginationProps {
  page: number
  pageCount: number
  /** While a page loads, or with nothing to page through. */
  disabled: boolean
  onPage: (page: number) => void
  /** Left of the buttons: totals and notes. */
  summary: ReactNode
}

/** First / previous / a page number to jump to / next / last. */
export function LibraryPagination({ page, pageCount, disabled, onPage, summary }: LibraryPaginationProps) {
  const [draft, setDraft] = useState(String(page))
  useEffect(() => setDraft(String(page)), [page])

  function commit() {
    const wanted = Math.floor(Number(draft))
    if (!Number.isFinite(wanted) || wanted < 1) { setDraft(String(page)); return }
    const next = Math.min(wanted, pageCount)
    setDraft(String(next))
    if (next !== page) onPage(next)
  }

  return <div className="mg-pagination lib-pagination">
    <span className="lib-page-summary">{summary}</span>
    <nav className="lib-pager" aria-label="翻页">
      <button type="button" aria-label="第一页" title="第一页" disabled={disabled || page <= 1} onClick={() => onPage(1)}><ChevronsLeft size={16} /></button>
      <button type="button" aria-label="上一页" title="上一页" disabled={disabled || page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={16} /></button>
      <label className="lib-page-jump">
        <span>第</span>
        <input
          type="number"
          inputMode="numeric"
          aria-label="页码"
          min={1}
          max={pageCount}
          disabled={disabled}
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); commit() } }}
        />
        <span>/ {pageCount} 页</span>
      </label>
      <button type="button" aria-label="下一页" title="下一页" disabled={disabled || page >= pageCount} onClick={() => onPage(page + 1)}><ChevronRight size={16} /></button>
      <button type="button" aria-label="最后一页" title="最后一页" disabled={disabled || page >= pageCount} onClick={() => onPage(pageCount)}><ChevronsRight size={16} /></button>
    </nav>
  </div>
}
