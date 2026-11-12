import { useCallback, useState } from 'react'

/**
 * A view preference remembered in this browser (localStorage, JSON). Storage that is unavailable
 * (private mode, blocked site data) or holds something `accept` rejects gives the fallback; a
 * write that fails keeps the value for this visit only.
 */
export function usePreference<T>(key: string, fallback: T, accept: (value: unknown) => value is T): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key)
      if (raw !== null) {
        const stored: unknown = JSON.parse(raw)
        if (accept(stored)) return stored
      }
    } catch { /* the fallback */ }
    return fallback
  })
  const update = useCallback((next: T) => {
    setValue(next)
    try { window.localStorage.setItem(key, JSON.stringify(next)) } catch { /* this visit only */ }
  }, [key])
  return [value, update]
}

/** For usePreference: one of the given values. */
export function oneOf<T extends string | number>(values: readonly T[]) {
  return (value: unknown): value is T => values.includes(value as T)
}
