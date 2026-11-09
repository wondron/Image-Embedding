import { untrusted } from '../../shared/lib/parse'
import type { EnumItemWire, EnumListResponseWire, EnumResponseWire, EnumValueWire } from '../../shared/api'

/** One stored value. Existing rows are read-only in this UI. */
export interface EnumValue {
  value: string
  label: string
  description: string
}

/** One enum definition from GET /api/enums or GET /api/enums/{name}. */
export interface EnumItem {
  name: string
  label: string
  description: string
  /** Server flag. Missing or non-boolean is treated as not editable. */
  editable: boolean
  values: EnumValue[]
}

export interface EnumListResult {
  enums: EnumItem[]
  /** Trimmed file_error, or null when the list is healthy. */
  fileError: string | null
  traceId: string | null
}

/** Detail / write body. file_error is not part of this contract and is not read. */
export interface EnumDetailResult {
  item: EnumItem
  traceId: string | null
}

export function toEnumValue(value: unknown): EnumValue | null {
  const body = untrusted<EnumValueWire>(value)
  if (!body || typeof body.value !== 'string' || body.value.trim() === '') return null
  return {
    value: body.value,
    label: typeof body.label === 'string' ? body.label : '',
    description: typeof body.description === 'string' ? body.description : '',
  }
}

export function toEnumItem(value: unknown): EnumItem | null {
  const body = untrusted<EnumItemWire>(value)
  if (!body || typeof body.name !== 'string' || body.name.trim() === '') return null
  if (typeof body.label !== 'string') return null
  const values = Array.isArray(body.values)
    ? body.values.flatMap(entry => {
      const row = toEnumValue(entry)
      return row ? [row] : []
    })
    : []
  return {
    name: body.name.trim(),
    label: body.label,
    description: typeof body.description === 'string' ? body.description : '',
    editable: body.editable === true,
    values,
  }
}

export function toEnumList(value: unknown, traceId: string | null): EnumListResult | null {
  const body = untrusted<EnumListResponseWire>(value)
  if (!body || body.ok !== true || !Array.isArray(body.enums)) return null
  const fileError = typeof body.file_error === 'string' && body.file_error.trim()
    ? body.file_error.trim()
    : null
  return {
    enums: body.enums.flatMap(entry => {
      const item = toEnumItem(entry)
      return item ? [item] : []
    }),
    fileError,
    traceId,
  }
}

export function toEnumDetail(value: unknown, traceId: string | null): EnumDetailResult | null {
  const body = untrusted<EnumResponseWire>(value)
  if (!body || body.ok !== true) return null
  const item = toEnumItem(body.enum)
  if (!item) return null
  return { item, traceId }
}
