import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

type Props = {
  icon: LucideIcon
  title: string
  description: string
  children?: ReactNode
  className?: string
}

/** Shared empty panel — no business scene/FlyB branching here. */
export function EmptyState({ icon: Icon, title, description, children, className = 'mg-empty' }: Props) {
  return (
    <div className={className}>
      <span className="mg-empty-icon"><Icon size={29} strokeWidth={1.4} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  )
}