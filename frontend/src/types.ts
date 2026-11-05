export interface SearchImage {
  id: string
  title: string
  imageUrl: string | null
  thumbnailUrl: string | null
  similarity: number | null
  category: string | null
  /** Scene-specific label when present (prefer over category in UI). */
  sceneLabel?: string | null
  /** Heating group name/code when outputs_heating. */
  heatingGroup?: string | null
  /** raw | cooked | frozen */
  foodState?: string | null
  /** From item or response when scene outputs heating. */
  outputsHeating?: boolean
  pageUrl: string | null
  siteName: string | null
  snippets: string[]
  width: number | null
  height: number | null
  /**
   * Retrieval channel from API `source` (contract: self | flyb | empty).
   * Never invent when the field is absent.
   */
  source?: 'self' | 'flyb' | 'empty' | string
  raw: Record<string, unknown>
}

export interface SearchResponse {
  items: SearchImage[]
  traceId: string | null
  status?: string
  message?: string
  /** Response-level channel: self | flyb | empty (contract SSOT). */
  source?: 'self' | 'flyb' | 'empty' | string
  /** Echo / flag when search scene has heating outputs. */
  outputsHeating?: boolean
  scene?: string
}