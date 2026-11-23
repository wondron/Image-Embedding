export {
  type SearchChannel,
  type SearchFallback,
  type SearchFallbackStatus,
  type SearchImage,
  type SearchRegion,
  type SearchResponse,
  displayCategoryLabel,
  getImageSources,
  getThumbnailSources,
  hasImage,
} from './model'
export { parseSimilarity, toSearchResponse } from './mapper'
export { fromPredictResponse } from './legacy-predict'
export { toAutoSearchResponse } from './auto-search'
export { type BatchSearchOutcome, type BatchSearchResponse, toBatchSearchResponse } from './batch-search'
export {
  type AutoSearchOptions,
  type BatchSearchOptions,
  type SearchOptions,
  AUTO_TOP_K_DEFAULT,
  AUTO_TOP_K_MAX,
  AUTO_TOP_K_MIN,
  BATCH_SEARCH_MAX_IMAGES,
  SEARCH_NUM_DEFAULT,
  SEARCH_NUM_MAX,
  SEARCH_NUM_MIN,
  searchAutoImages,
  searchGlobalImages,
  searchLocalBatch,
  searchLocalImages,
} from './api'
export { SourceBadge } from './SourceBadge'
