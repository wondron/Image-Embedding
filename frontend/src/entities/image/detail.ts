/**
 * One stored image as the gallery admin reads and edits it (backend app/schemas/images.py):
 * GET / PATCH / DELETE /api/images/{image_id}, POST /api/images/{image_id}/vector-sync.
 */
import { asId, asInteger, asObject, asPositiveNumber, asText, asTextList, untrusted, untrustedList, type Untrusted } from '../../shared/lib/parse'
import { safeHttpUrl } from '../../shared/lib/safeHttpUrl'
import type { ImageDetailWire, ImageLedgerEntryWire, ImageVectorStateWire } from '../../shared/api'
import {
  parseDescription,
  parseFoodState,
  parseHeatingGroup,
  parseLabelStatus,
  parseSceneCodes,
  type FoodState,
  type HeatingGroup,
  type LabelStatus,
} from './model'

/** One image_embeddings row: the image's point is in this model version's collection. */
export interface ImageLedgerEntry {
  modelId: number
  name: string
  version: string
  collection: string
  /** building / ready / current / retired */
  modelStatus: string
  syncedVersion: number | null
  embeddedAt: string | null
}

export interface ImageDetail {
  id: string
  /** Hex SHA-256 of the original. */
  sha256: string | null
  /** Thumbnail ← `thumb_url`. */
  imageUrl: string | null
  originalUrl: string | null
  width: number | null
  height: number | null
  categoryId: number | null
  /** The category's zh primary name. */
  category: string | null
  categoryCode: string | null
  labelStatus: LabelStatus | null
  scenes: string[]
  description: string
  tags: string[]
  sourceType: string | null
  license: string | null
  foodState: FoodState | null
  foodProcessing: string | null
  foodCount: number | null
  totalMassG: number | null
  waterMassG: number | null
  mainIngredientMassG: number | null
  heatingGroup: HeatingGroup | null
  payloadVersion: number | null
  createdAt: string | null
  updatedAt: string | null
  embeddings: ImageLedgerEntry[]
}

/**
 * The image in the current model's collection: synced (payload matches PostgreSQL), stale
 * (payload behind: push it), missing (no point: index again), unavailable (could not read).
 */
export type VectorStatus = 'synced' | 'stale' | 'missing' | 'unavailable'

export interface ImageVectorState {
  status: VectorStatus
  collection: string | null
  embeddingSpaceId: string | null
  exists: boolean | null
  /** The point's payload as stored in the vector store. */
  payload: Record<string, unknown> | null
  /** The payload PostgreSQL gives now. */
  expectedPayload: Record<string, unknown> | null
  payloadMatches: boolean | null
  payloadVersion: number | null
  syncedVersion: number | null
  embeddedAt: string | null
  /** Why the state is unavailable. */
  detail: string | null
}

/** How a write reached the search index (same values as the scene membership writes). */
export type VectorSyncOutcome = 'pushed' | 'missing' | 'not_ready' | 'failed'

const VECTOR_STATUSES: readonly VectorStatus[] = ['synced', 'stale', 'missing', 'unavailable']
const VECTOR_SYNCS: readonly VectorSyncOutcome[] = ['pushed', 'missing', 'not_ready', 'failed']

function nonNegative(value: unknown): number | null {
  const number = asInteger(value)
  return number !== null && number >= 0 ? number : null
}

function toLedgerEntry(entry: Untrusted<ImageLedgerEntryWire>): ImageLedgerEntry | null {
  const modelId = asInteger(entry.model_id)
  if (modelId === null) return null
  return {
    modelId,
    name: asText(entry.name) ?? '',
    version: asText(entry.version) ?? '',
    collection: asText(entry.collection) ?? '',
    modelStatus: asText(entry.model_status) ?? '',
    syncedVersion: asInteger(entry.synced_version),
    embeddedAt: asText(entry.embedded_at),
  }
}

/** The `image` of a detail / patch body; null when it has no id. */
export function toImageDetail(value: unknown): ImageDetail | null {
  const image = untrusted<ImageDetailWire>(value)
  const id = asId(image?.id)
  if (!image || !id) return null
  return {
    id,
    sha256: asText(image.sha256),
    imageUrl: safeHttpUrl(image.thumb_url),
    originalUrl: safeHttpUrl(image.original_url),
    width: asPositiveNumber(image.width),
    height: asPositiveNumber(image.height),
    categoryId: asInteger(image.category_id),
    category: asText(image.category),
    categoryCode: asText(image.category_code),
    labelStatus: parseLabelStatus(image.label_status),
    scenes: parseSceneCodes(image.scenes),
    description: parseDescription(image.description),
    tags: asTextList(image.tags),
    sourceType: asText(image.source_type),
    license: asText(image.license),
    foodState: parseFoodState(image.food_state),
    foodProcessing: asText(image.food_processing),
    foodCount: nonNegative(image.food_count),
    totalMassG: nonNegative(image.total_mass_g),
    waterMassG: nonNegative(image.water_mass_g),
    mainIngredientMassG: nonNegative(image.main_ingredient_mass_g),
    heatingGroup: parseHeatingGroup(image.heating_group),
    payloadVersion: asInteger(image.payload_version),
    createdAt: asText(image.created_at),
    updatedAt: asText(image.updated_at),
    embeddings: untrustedList<ImageLedgerEntryWire>(image.embeddings)
      .map(toLedgerEntry)
      .filter((entry): entry is ImageLedgerEntry => entry !== null),
  }
}

/** The `vector` of a detail body; anything off-contract reads as unavailable. */
export function toVectorState(value: unknown): ImageVectorState {
  const vector = untrusted<ImageVectorStateWire>(value)
  const status = VECTOR_STATUSES.includes(vector?.status as VectorStatus) ? vector!.status as VectorStatus : 'unavailable'
  return {
    status,
    collection: asText(vector?.collection),
    embeddingSpaceId: asText(vector?.embedding_space_id),
    exists: typeof vector?.exists === 'boolean' ? vector.exists : null,
    payload: asObject(vector?.payload),
    expectedPayload: asObject(vector?.expected_payload),
    payloadMatches: typeof vector?.payload_matches === 'boolean' ? vector.payload_matches : null,
    payloadVersion: asInteger(vector?.payload_version),
    syncedVersion: asInteger(vector?.synced_version),
    embeddedAt: asText(vector?.embedded_at),
    detail: asText(vector?.detail) ?? (vector ? null : '服务器没有返回向量状态。'),
  }
}

export function toVectorSync(value: unknown): VectorSyncOutcome | null {
  return VECTOR_SYNCS.includes(value as VectorSyncOutcome) ? value as VectorSyncOutcome : null
}

/**
 * Where the stored payload differs from what PostgreSQL gives: [field, stored, expected] per
 * field, scenes compared as a set (search only asks "contains").
 */
export function payloadDifferences(vector: Pick<ImageVectorState, 'payload' | 'expectedPayload'>): Array<[string, unknown, unknown]> {
  if (!vector.payload || !vector.expectedPayload) return []
  const out: Array<[string, unknown, unknown]> = []
  for (const [key, expected] of Object.entries(vector.expectedPayload)) {
    const stored = vector.payload[key] ?? null
    const same = key === 'scenes' && Array.isArray(stored) && Array.isArray(expected)
      ? [...stored].map(String).sort().join('\n') === [...expected].map(String).sort().join('\n')
      : JSON.stringify(stored) === JSON.stringify(expected ?? null)
    if (!same) out.push([key, stored, expected ?? null])
  }
  return out
}
