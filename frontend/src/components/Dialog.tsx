import { useLayoutEffect, useId, useRef, type ReactNode } from 'react'
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
  useLayoutEffect(() => {
    const element = ref.current!
    const doc = element.ownerDocument
    const opener = doc.activeElement instanceof HTMLElement ? doc.activeElement : null
    const scopes: HTMLElement[] = []
    for (let scope = opener?.parentElement; scope && scope !== doc.body; scope = scope.parentElement) {
      scopes.push(scope)
    }
    element.showModal()
    element.querySelector<HTMLElement>('[data-dialog-initial-focus]')?.focus()
    return () => {
      // Close before React removes the dialog from the DOM/top layer.
      if (element.open) element.close()
      // Wait for removal and the same commit's disabled/visibility updates.
      queueMicrotask(() => {
        // StrictMode replays setup/cleanup without removing this dialog.
        if (element.isConnected) return
        const active = doc.activeElement
        // Respect focus deliberately moved by navigation or a replacement dialog.
        if (active instanceof HTMLElement && active !== doc.body && active !== opener
          && active !== doc.documentElement && !element.contains(active)) return
        const modals = Array.from(doc.querySelectorAll<HTMLDialogElement>('dialog:modal'))
        function focus(target: HTMLElement): boolean {
          if (!target.isConnected || target === doc.body || target === doc.documentElement
            || element.contains(target) || target.matches(':disabled')
            || target.closest('[inert], [hidden], [aria-disabled="true"]')
            || !target.getClientRects().length || getComputedStyle(target).visibility === 'hidden'
            || modals.some(modal => !modal.contains(target))) return false
          if (doc.activeElement !== target) target.focus()
          return doc.activeElement === target
        }
        if (opener && focus(opener)) return
        // Deleted/disabled trigger: nearest surviving context's usable control,
        // never the large page container or a control behind another modal.
        for (const scope of scopes) {
          if (!scope.isConnected) continue
          for (const target of scope.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex]')) {
            if (target.tabIndex >= 0 && focus(target)) return
          }
        }
      })
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
