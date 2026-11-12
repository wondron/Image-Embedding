import { createContext, useContext } from 'react'

/** The API base URL the app talks to ('' = same origin), provided once at the root (App). */
export const ApiBaseContext = createContext('')

export function useApiBase(): string {
  return useContext(ApiBaseContext)
}
