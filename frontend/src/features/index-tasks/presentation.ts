export function taskTime(value: string | Date | null): string {
  if (value === null) return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return `${date.toLocaleString('zh-CN', { timeZone: 'America/Denver', hour12: false })} MST`
}
export function taskDuration(value: number | null): string { return value === null ? '—' : `${value}ms` }
export function taskNewImage(value: boolean | null): string { return value === null ? '—' : value ? '新图' : '已存在，沿用标注' }
