import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react'
import { CheckCircle2, ExternalLink, Info, TriangleAlert, X, XCircle, type LucideIcon } from 'lucide-react'
import { noticeStore, type NoticeType } from './notice'
import './status-overlay.css'

const BADGE: Readonly<Record<NoticeType, LucideIcon>> = { success: CheckCircle2, info: Info, warn: TriangleAlert, error: XCircle }

/**
 * The one popup of the app (mount it once, at the root). It shows what notify() signals: a modal
 * <dialog>, so it sits above any dialog already open; the rest of the page waits until it closes.
 * One that closes by itself counts down along its bottom edge and holds while the pointer is on it.
 */
export function StatusOverlay() {
  const notice = useSyncExternalStore(noticeStore.subscribe, noticeStore.current, noticeStore.current)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const okRef = useRef<HTMLButtonElement>(null)
  /** What had the focus before the popup opened; it gets it back. */
  const opener = useRef<HTMLElement | null>(null)
  /** The last control focused outside the popup: the one that started an action is often disabled while it runs. */
  const lastFocused = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const messageId = useId()
  const [paused, setPaused] = useState(false)
  /** Milliseconds the shown popup has left, and whose they are. */
  const left = useRef(0)
  const leftOf = useRef<number | null>(null)

  useEffect(() => {
    const remember = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement && !dialogRef.current?.contains(event.target)) lastFocused.current = event.target
    }
    document.addEventListener('focusin', remember, true)
    return () => document.removeEventListener('focusin', remember, true)
  }, [])

  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (notice) {
      leftOf.current = notice.id
      left.current = notice.duration
      setPaused(false)
      if (!dialog.open) {
        const active = document.activeElement
        // Nothing focused (the button that started it was disabled meanwhile): the last control used
        opener.current = active instanceof HTMLElement && active !== document.body ? active : lastFocused.current
        dialog.showModal()
      }
      okRef.current?.focus()
    } else if (dialog.open) {
      dialog.close()
      const target = opener.current
      opener.current = null
      // Back where the person was, unless that control is gone or now behind another modal
      if (target?.isConnected && !target.closest('[inert]') && !target.matches(':disabled')) target.focus({ preventScroll: true })
    }
  }, [notice])

  useEffect(() => {
    if (!notice || notice.duration <= 0 || paused) return
    const id = notice.id
    const started = performance.now()
    const timer = window.setTimeout(() => noticeStore.close(id), Math.max(0, left.current))
    return () => {
      window.clearTimeout(timer)
      if (leftOf.current === id) left.current -= performance.now() - started
    }
  }, [notice, paused])

  const close = () => { if (notice) noticeStore.close(notice.id) }
  const Badge = notice ? BADGE[notice.type] : Info
  const stays = Boolean(notice && (notice.type === 'error' || notice.type === 'warn'))

  return <dialog
    ref={dialogRef}
    className={`status-overlay${notice ? ` is-${notice.type}` : ''}`}
    role={stays ? 'alertdialog' : 'dialog'}
    aria-labelledby={titleId}
    aria-describedby={messageId}
    onCancel={event => { event.preventDefault(); close() }}
    onClick={event => { if (event.target === event.currentTarget && notice?.closable) close() }}
  >
    {notice && <div
      className="so-panel"
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
    >
      <div className="so-fx" aria-hidden="true" />
      <header className="so-head">
        <span className="so-badge" aria-hidden="true"><Badge size={17} strokeWidth={2.2} /></span>
        <h2 className="so-title" id={titleId}>{notice.title}</h2>
        <button type="button" className="so-close" aria-label="关闭提示" title="关闭" onClick={close}><X size={16} /></button>
      </header>
      <div className="so-body">
        <p className="so-message" id={messageId}>{notice.message}</p>
        {notice.details.length > 0 && <ul className="so-details">{notice.details.map((line, index) => <li key={index}>{line}</li>)}</ul>}
      </div>
      <footer className="so-foot">
        {notice.action?.href && <a className="so-action" href={notice.action.href} target="_blank" rel="noreferrer" onClick={close}>{notice.action.label}<ExternalLink size={13} /></a>}
        {notice.action && !notice.action.href && <button type="button" className="so-action" onClick={() => { notice.action?.onClick?.(); close() }}>{notice.action.label}</button>}
        <button type="button" ref={okRef} className="so-ok" onClick={close}>知道了</button>
      </footer>
      {notice.duration > 0 && <span
        key={notice.id}
        className={`so-countdown${paused ? ' is-paused' : ''}`}
        style={{ '--so-duration': `${notice.duration}ms` } as CSSProperties}
        aria-hidden="true"
      />}
    </div>}
  </dialog>
}
