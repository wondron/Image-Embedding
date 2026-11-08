/**
 * Defensive readers for untrusted JSON (API responses).
 * Every mapper reads wire values through these, so one kind of value is read the same way everywhere.
 */

export type JsonObject = Record<string, unknown>

/**
 * A response object whose fields are not checked yet: only the contract's keys can be read,
 * each as `unknown`. Reading a key the contract does not define is a compile error.
 */
export type Untrusted<T> = { readonly [K in keyof T]?: unknown }

/** Plain object (not null, not an array), else null. */
export function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : null
}

/** A plain object viewed through the contract type `T`, else null. */
export function untrusted<T>(value: unknown): Untrusted<T> | null {
  return asObject(value) as Untrusted<T> | null
}

/** The plain objects of an array (other entries dropped); [] when not an array. */
export function untrustedList<T>(value: unknown): Untrusted<T>[] {
  if (!Array.isArray(value)) return []
  return value.map(entry => untrusted<T>(entry)).filter((entry): entry is Untrusted<T> => entry !== null)
}

/** Trimmed non-empty string, else null. Numbers are not text. */
export function asText(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed || null
}

/** Identifier: a non-empty string, or a finite number written as a string. */
export function asId(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return asText(value)
}

/** Strings of an array, trimmed, with blank and non-string entries dropped. */
export function asTextList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(asText).filter((entry): entry is string => entry !== null)
}

/** Finite number, else null (numeric strings are not numbers). */
export function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Finite number greater than zero, else null. */
export function asPositiveNumber(value: unknown): number | null {
  const number = asFiniteNumber(value)
  return number !== null && number > 0 ? number : null
}

/** Integer, else null. */
export function asInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null
}

/** Boolean, else undefined (so a missing flag is never read as false). */
export function asBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined
}
