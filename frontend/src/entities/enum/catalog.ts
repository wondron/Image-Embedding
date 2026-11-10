import type { EnumItem } from './model'

/**
 * GET /api/enums viewed for display: every enum by name, values in file order.
 * Lookups never throw and never guess: a value the catalog does not know is shown as stored.
 */
export interface EnumCatalog {
  readonly items: ReadonlyMap<string, EnumItem>
}

export interface EnumOption {
  value: string
  label: string
}

export const EMPTY_ENUM_CATALOG: EnumCatalog = { items: new Map() }

export function toEnumCatalog(items: readonly EnumItem[]): EnumCatalog {
  return { items: new Map(items.map(item => [item.name, item])) }
}

/** The server label of one stored value; null when the catalog has no non-blank label for it. */
export function enumValueLabel(catalog: EnumCatalog, name: string, value: string): string | null {
  const label = catalog.items.get(name)?.values.find(row => row.value === value)?.label.trim()
  return label || null
}

/**
 * Text for a stored value: 「—」 when there is none, else its label, else the value itself.
 * Editable enums gain values at runtime, so a value whose label is not loaded is still shown.
 */
export function enumValueText(catalog: EnumCatalog, name: string, value: string | null | undefined): string {
  if (!value) return '—'
  return enumValueLabel(catalog, name, value) ?? value
}

/** Select options in file order; `current` is appended when the catalog lacks it, so a select can show it. */
export function enumOptions(catalog: EnumCatalog, name: string, current?: string | null): EnumOption[] {
  const options = (catalog.items.get(name)?.values ?? []).map(row => ({
    value: row.value,
    label: row.label.trim() || row.value,
  }))
  if (current && !options.some(option => option.value === current)) options.push({ value: current, label: current })
  return options
}
