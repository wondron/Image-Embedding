import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

export function Dialog({ title, children, onClose, wide = false, footer }: {
  title: string; children: ReactNode; onClose: () => void; wide?: boolean; footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = ref.current!
    element.showModal()
    return () => element.close()
  }, [])
  return <dialog ref={ref} className={`dialog ${wide ? 'dialog-wide' : ''}`} onCancel={onClose}
    onClick={event => { if (event.target === event.currentTarget) onClose() }} aria-labelledby="dialog-title">
    <div className="dialog-heading"><h2 id="dialog-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭弹窗"><X size={20} /></button></div>
    <div className="dialog-body">{children}</div>
    {footer && <div className="dialog-footer">{footer}</div>}
  </dialog>
}
