export interface SearchImage {
  id: string;
  title: string;
  imageUrl: string | null;
  thumbnailUrl: string | null;
  similarity: number | null;
  category: string | null;
  pageUrl: string | null;
  siteName: string | null;
  snippets: string[];
  width: number | null;
  height: number | null;
  /**
   * Retrieval channel from API `source` (contract: self | flyb | empty).
   * Never invent when the field is absent.
   */
  source?: 'self' | 'flyb' | 'empty' | string;
  raw: Record<string, unknown>;
}

export interface SearchResponse {
  items: SearchImage[];
  traceId: string | null;
  status?: string;
  message?: string;
  /** Response-level channel: self | flyb | empty (contract SSOT). */
  source?: 'self' | 'flyb' | 'empty' | string;
}
