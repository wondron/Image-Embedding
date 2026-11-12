import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, ExternalLink, Info, TriangleAlert, X, XCircle, type LucideIcon } from 'lucide-react'
import { NOTICE_TITLE, noticeStore, type Notice, type NoticeType } from './notice'
import './status-overlay.css'

const BADGE: Readonly<Record<NoticeType, LucideIcon>> = { success: CheckCircle2, info: Info, warn: TriangleAlert, error: XCircle }

/**
 * Counts a notice down while it shows: closes it when its time is up, holding while `paused`.
 * Nothing for a notice that stays (duration 0).
 */
function useCountdown(notice: Notice | null, paused: boolean) {
  const left = useRef(0)
  const leftOf = useRef<number | null>(null)
  if (notice && leftOf.current !== notice.id) {
    leftOf.current = notice.id
    left.current = notice.duration
  }
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
}

function Countdown({ notice, paused }: { notice: Notice; paused: boolean }) {
  return notice.duration > 0
    ? <span
      className={`so-countdown${paused ? ' is-paused' : ''}`}
      style={{ '--so-duration': `${notice.duration}ms` } as CSSProperties}
      aria-hidden="true"
    />
    : null
}

function ActionButton({ notice, className }: { notice: Notice; className: string }) {
  const close = () => noticeStore.close(notice.id)
  if (!notice.action) return null
  return notice.action.href
    ? <a className={className} href={notice.action.href} target="_blank" rel="noreferrer" onClick={close}>{notice.action.label}<ExternalLink size={13} /></a>
    : <button type="button" className={className} onClick={() => { notice.action?.onClick?.(); close() }}>{notice.action.label}</button>
}

/** One light toast: never takes the focus or blocks the page; closes by itself, holding while hovered. */
function Toast({ notice }: { notice: Notice }) {
  const [paused, setPaused] = useState(false)
  useCountdown(notice, paused)
  const Icon = BADGE[notice.type]
  return <div className={`so-toast is-${notice.type}`} onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}>
    <span className="so-toast-icon" aria-hidden="true"><Icon size={17} strokeWidth={2.2} /></span>
    <p className="so-toast-text">
      {notice.title !== NOTICE_TITLE[notice.type] && <strong>{notice.title}</strong>}
      <span>{notice.message}</span>
      {notice.details.map((line, index) => <small key={index}>{line}</small>)}
    </p>
    <ActionButton notice={notice} className="so-toast-action" />
    <button type="button" className="so-toast-close" aria-label="关闭提示" title="关闭" onClick={() => noticeStore.close(notice.id)}><X size={14} /></button>
    <Countdown notice={notice} paused={paused} />
  </div>
}

/**
 * Where toasts can be seen and used: inside the topmost open modal dialog (everything outside it is
 * inert, and behind its backdrop), else the body. The popup itself is not one of them.
 */
function toastHost(popup: Element | null): Element {
  let modals: Element[]
  try { modals = Array.from(document.querySelectorAll('dialog:modal')) } catch { modals = Array.from(document.querySelectorAll('dialog[open]')) }
  return modals.filter(dialog => dialog !== popup).at(-1) ?? document.body
}

function ToastStack({ toasts, popup }: { toasts: readonly Notice[]; popup: RefObject<HTMLDialogElement | null> }) {
  const [host, setHost] = useState<Element | null>(null)
  const hostRef = useRef<Element | null>(null)
  /** The live region exists in its host before toasts go in, so screen readers announce them. */
  const [ready, setReady] = useState(false)
  const showing = toasts.length > 0

  useLayoutEffect(() => {
    const update = () => {
      const next = toastHost(popup.current)
      if (next === hostRef.current) return
      hostRef.current = next
      setHost(next)
      setReady(false)
    }
    update()
    if (!showing) return
    // Follow dialogs opening and closing while toasts show
    const observer = new MutationObserver(update)
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open'] })
    return () => observer.disconnect()
  }, [showing, popup])

  useEffect(() => {
    if (ready || !host) return
    const frame = window.requestAnimationFrame(() => setReady(true))
    return () => window.cancelAnimationFrame(frame)
  }, [host, ready])

  if (!host) return null
  return createPortal(
    <div className="so-toasts" role="status" aria-live="polite">{ready && toasts.map(notice => <Toast key={notice.id} notice={notice} />)}</div>,
    host,
  )
}

/**
 * The one feedback control of the app (mount it once, at the root); it shows what notify() signals.
 * Success and info: light toasts at the top that never block. Warnings and errors: a modal <dialog>
 * above any dialog already open, which stays until the person closes it.
 */
export function StatusOverlay() {
  const notice = useSyncExternalStore(noticeStore.subscribe, noticeStore.current, noticeStore.current)
  const toasts = useSyncExternalStore(noticeStore.subscribe, noticeStore.toasts, noticeStore.toasts)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const okRef = useRef<HTMLButtonElement>(null)
  /** What had the focus before the popup opened; it gets it back. */
  const opener = useRef<HTMLElement | null>(null)
  /** The last control focused outside the popup: the one that started an action is often disabled while it runs. */
  const lastFocused = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const messageId = useId()
  const [paused, setPaused] = useState(false)
  useCountdown(notice, paused)

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

  const close = () => { if (notice) noticeStore.close(notice.id) }
  const Badge = notice ? BADGE[notice.type] : Info
  const stays = Boolean(notice && (notice.type === 'error' || notice.type === 'warn'))

  return <>
    <dialog
      ref={dialogRef}
      className={`status-overlay${notice ? ` is-${notice.type}` : ''}`}
      role={stays ? 'alertdialog' : 'dialog'}
      aria-labelledby={titleId}
      aria-describedby={messageId}
      onCancel={event => { event.preventDefault(); close() }}
      onClick={event => { if (event.target === event.currentTarget && notice?.closable) close() }}
    >
      {notice && <div className="so-panel" onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}>
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
          <ActionButton notice={notice} className="so-action" />
          <button type="button" ref={okRef} className="so-ok" onClick={close}>知道了</button>
        </footer>
        <Countdown key={notice.id} notice={notice} paused={paused} />
      </div>}
    </dialog>
    <ToastStack toasts={toasts} popup={dialogRef} />
  </>
}
