export interface SearchImage {
  id: string
  /** UI display name derived from category / sceneLabel — never from wire `title` (P5). */
  title: string
  /** Mapped from wire `original_url` only. */
  imageUrl: string | null
  /** Mapped from wire `thumb_url` only. */
  thumbnailUrl: string | null
  similarity: number | null
  category: string | null
  categoryId?: number | null
  scenes?: string[]
  /** Scene-specific label when present (prefer over category in UI). */
  sceneLabel?: string | null
  /** Heating group name/code when envelope outputs_heating. */
  heatingGroup?: string | null
  /** raw | cooked | frozen */
  foodState?: string | null
  /** Propagated from envelope `outputs_heating` only (P9). */
  outputsHeating?: boolean
  /** Always null — FlyB shell removed (P7). */
  pageUrl: string | null
  /** Always null — FlyB shell removed (P7). */
  siteName: string | null
  /** Always [] — FlyB shell removed (P7). */
  snippets: string[]
  width: number | null
  height: number | null
  /**
   * Echo of envelope `source` onto the card for badges (not a hit-level wire field).
   * Contract channel: self | empty (flyb legacy only, never invent).
   */
  source?: 'self' | 'flyb' | 'empty' | string
  raw: Record<string, unknown>
}

export interface SearchResponse {
  items: SearchImage[]
  traceId: string | null
  status?: string
  message?: string
  /** Envelope channel: self | empty (flyb legacy only). */
  source?: 'self' | 'flyb' | 'empty' | string
  /** Envelope `outputs_heating` only — never invent from scene seed (P9). */
  outputsHeating?: boolean
  scene?: string
}
