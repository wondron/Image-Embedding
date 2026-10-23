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
  raw: Record<string, unknown>;
}

export interface SearchResponse {
  items: SearchImage[];
  traceId: string | null;
  status?: string;
  message?: string;
}
