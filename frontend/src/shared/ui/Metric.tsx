import type { LucideIcon } from 'lucide-react'

export function Metric({
  label, value, detail, icon: Icon,
}: {
  label: string
  value: string
  detail: string
  icon: LucideIcon
}) {
  return (
    <div className="mg-metric">
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
      <span className="mg-metric-icon"><Icon size={22} strokeWidth={1.5} /></span>
    </div>
  )
}