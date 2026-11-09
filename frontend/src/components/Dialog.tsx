import { useEffect, useId, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

export function Dialog({
  title, children, onClose, wide = false, size, footer, dismissDisabled = false,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  /** Existing large layout. New callers can pass size="lg" instead. */
  wide?: boolean
  /** sm uses the default dialog width; lg uses the existing wide dialog. There is no md size. */
  size?: 'sm' | 'lg'
  footer?: ReactNode
  /** Keeps the dialog open while a non-idempotent submit is in flight. */
  dismissDisabled?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const large = size ? size === 'lg' : wide
  function requestClose() {
    if (dismissDisabled) return
    onClose()
  }
  useEffect(() => {
    const element = ref.current!
    element.showModal()
    element.querySelector<HTMLElement>('[data-dialog-initial-focus]')?.focus()
    return () => {
      if (element.open) element.close()
    }
  }, [])
  return <dialog
    ref={ref}
    className={`dialog ${large ? 'dialog-wide' : ''}`}
    onCancel={event => {
      if (dismissDisabled) event.preventDefault()
      else onClose()
    }}
    onClick={event => { if (event.target === event.currentTarget) requestClose() }}
    aria-labelledby={titleId}
  >
    <div className="dialog-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" onClick={requestClose} disabled={dismissDisabled} aria-label="关闭弹窗"><X size={20} /></button></div>
    <div className="dialog-body">{children}</div>
    {footer && <div className="dialog-footer">{footer}</div>}
  </dialog>
}
