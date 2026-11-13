/**
 * Model versions (backend app/schemas/embedding_models.py): one embedding model version per row,
 * each with its own vector collection; which one search uses, which one EMBEDDING_CONFIG names,
 * and the progress of filling a version's collection.
 *
 * Parsing is strict: these screens switch search to another collection and drop collections, so
 * a row that does not read cleanly makes the whole answer unreadable instead of being skipped.
 */
import { asBoolean, asInteger, asText, untrusted } from '../../shared/lib/parse'
import type {
  EmbeddingBuildFailureWire,
  EmbeddingBuildWire,
  EmbeddingConfiguredWire,
  EmbeddingModelWire,
  EmbeddingModelsResponseWire,
  EmbeddingRetrievalWire,
} from '../../shared/api'

export type EmbeddingModelStatus = 'building' | 'ready' | 'current' | 'retired'
export const EMBEDDING_MODEL_STATUSES: readonly EmbeddingModelStatus[] = ['building', 'ready', 'current', 'retired']
/** configured: the EMBEDDING_CONFIG encoder; serving: the one search holds; recorded: loadable again from its fingerprint. */
export type EncoderSource = 'configured' | 'serving' | 'recorded'
export type BuildState = 'running' | 'stopping' | 'done' | 'stopped' | 'failed'
/** A version's weight files in this image: ok, missing (left the image), changed (replaced). */
export type WeightsStatus = 'ok' | 'missing' | 'changed'
const ENCODER_SOURCES: readonly EncoderSource[] = ['configured', 'serving', 'recorded']
const BUILD_STATES: readonly BuildState[] = ['running', 'stopping', 'done', 'stopped', 'failed']
const WEIGHTS_STATUSES: readonly WeightsStatus[] = ['ok', 'missing', 'changed']

export interface EmbeddingModel {
  id: number
  name: string
  version: string
  /** name:version, as the backend logs it. */
  label: string
  status: EmbeddingModelStatus
  collection: string
  dim: number
  distance: string
  weightsUri: string
  createdAt: string | null
  activatedAt: string | null
  embedded: number
  missing: number
  behind: number
  /** Points really in its collection (null: unreadable); fewer than embedded means vectors were lost. */
  points: number | null
  weights: WeightsStatus | null
  /** SHA-256 over its weight files. */
  artifact: string | null
  /** Its encoder fingerprint is recorded (without one, nobody can tell which encoder wrote it). */
  hasFingerprint: boolean
  /** EMBEDDING_CONFIG names this version's encoder. */
  configured: boolean
  /** This API process searches and indexes with it. */
  serving: boolean
  encoder: EncoderSource | null
}

export interface ConfiguredEncoder {
  loaded: boolean
  modelPath: string | null
  dim: number | null
  artifact: string | null
  /** The version it is; null: a model not registered yet. */
  modelId: number | null
  detail: string | null
}

export interface RetrievalPairing {
  ready: boolean
  modelId: number | null
  /** own: EMBEDDING_CONFIG names another model, search runs on this version's own encoder. */
  encoder: 'configured' | 'own' | null
  code: string | null
  detail: string | null
}

export interface BuildFailure {
  imageId: string
  code: string
  detail: string
}

export interface ModelBuild {
  modelId: number
  state: BuildState
  startedAt: string
  finishedAt: string | null
  /** Ledger rows checked against the collection first; repaired: of them, points that were lost. */
  verified: number
  repaired: number
  embedded: number
  failed: number
  gone: number
  payloads: number
  passes: number
  remaining: number | null
  error: string | null
  failures: BuildFailure[]
}

export interface ModelOverview {
  images: number
  configured: ConfiguredEncoder
  retrieval: RetrievalPairing
  build: ModelBuild | null
  /** A switch embeds at most this many missing images itself. */
  switchCatchUpMax: number
  models: EmbeddingModel[]
}

const count = (value: unknown): number | null => {
  const n = asInteger(value)
  return n !== null && n >= 0 ? n : null
}
const positive = (value: unknown): number | null => {
  const n = asInteger(value)
  return n !== null && n >= 1 ? n : null
}
const nullableText = (value: unknown): string | null | undefined => value === null ? null : asText(value) ?? undefined
const nullableId = (value: unknown): number | null | undefined => value === null ? null : positive(value) ?? undefined

function oneOf<T extends string>(values: readonly T[], value: unknown): T | null {
  return typeof value === 'string' && (values as readonly string[]).includes(value) ? value as T : null
}

export function toEmbeddingModel(value: unknown): EmbeddingModel | null {
  const raw = untrusted<EmbeddingModelWire>(value)
  if (!raw) return null
  const id = positive(raw.id)
  const name = asText(raw.name)
  const version = asText(raw.version)
  const status = oneOf(EMBEDDING_MODEL_STATUSES, raw.status)
  const collection = asText(raw.collection)
  const dim = positive(raw.dim)
  const distance = asText(raw.distance)
  const weightsUri = typeof raw.weights_uri === 'string' ? raw.weights_uri : null
  const embedded = count(raw.embedded)
  const missing = count(raw.missing)
  const behind = count(raw.behind)
  const points = raw.points === null ? null : count(raw.points)
  const weights = raw.weights === null ? null : oneOf(WEIGHTS_STATUSES, raw.weights)
  const artifact = nullableText(raw.artifact)
  const fingerprint = asBoolean(raw.fingerprint)
  const configured = asBoolean(raw.configured)
  const serving = asBoolean(raw.serving)
  const encoder = raw.encoder === null ? null : oneOf(ENCODER_SOURCES, raw.encoder)
  const createdAt = nullableText(raw.created_at)
  const activatedAt = nullableText(raw.activated_at)
  if (id === null || !name || !version || !status || !collection || dim === null || !distance || weightsUri === null
    || embedded === null || missing === null || behind === null || fingerprint === undefined || configured === undefined
    || serving === undefined || (raw.encoder !== null && encoder === null) || createdAt === undefined || activatedAt === undefined
    || (raw.points !== null && points === null) || (raw.weights !== null && weights === null) || artifact === undefined) return null
  return {
    id, name, version, label: `${name}:${version}`, status, collection, dim, distance, weightsUri,
    createdAt, activatedAt, embedded, missing, behind, points, weights, artifact,
    hasFingerprint: fingerprint, configured, serving, encoder,
  }
}

function toConfigured(value: unknown): ConfiguredEncoder | null {
  const raw = untrusted<EmbeddingConfiguredWire>(value)
  const loaded = asBoolean(raw?.loaded)
  const modelPath = nullableText(raw?.model_path)
  const dim = nullableId(raw?.dim)
  const artifact = nullableText(raw?.artifact)
  const modelId = nullableId(raw?.model_id)
  const detail = nullableText(raw?.detail)
  if (!raw || loaded === undefined || modelPath === undefined || dim === undefined || artifact === undefined
    || modelId === undefined || detail === undefined) return null
  return { loaded, modelPath, dim, artifact, modelId, detail }
}

function toRetrieval(value: unknown): RetrievalPairing | null {
  const raw = untrusted<EmbeddingRetrievalWire>(value)
  const ready = asBoolean(raw?.ready)
  const modelId = nullableId(raw?.model_id)
  const encoder = raw?.encoder === null ? null : oneOf(['configured', 'own'] as const, raw?.encoder)
  const code = nullableText(raw?.code)
  const detail = nullableText(raw?.detail)
  if (!raw || ready === undefined || modelId === undefined || (raw.encoder !== null && encoder === null)
    || code === undefined || detail === undefined || ready !== (modelId !== null)) return null
  return { ready, modelId, encoder, code, detail }
}

function toFailure(value: unknown): BuildFailure | null {
  const raw = untrusted<EmbeddingBuildFailureWire>(value)
  const imageId = asText(raw?.image_id)
  const code = asText(raw?.code)
  const detail = typeof raw?.detail === 'string' ? raw.detail : null
  return imageId && code && detail !== null ? { imageId, code, detail } : null
}

export function toModelBuild(value: unknown): ModelBuild | null {
  const raw = untrusted<EmbeddingBuildWire>(value)
  if (!raw) return null
  const modelId = positive(raw.model_id)
  const state = oneOf(BUILD_STATES, raw.state)
  const startedAt = asText(raw.started_at)
  const finishedAt = nullableText(raw.finished_at)
  const numbers = [raw.embedded, raw.failed, raw.gone, raw.payloads, raw.passes, raw.verified, raw.repaired].map(count)
  const remaining = raw.remaining === null ? null : count(raw.remaining)
  const error = nullableText(raw.error)
  const failures = Array.isArray(raw.failures) ? raw.failures.map(toFailure) : null
  if (modelId === null || !state || !startedAt || finishedAt === undefined || numbers.some(n => n === null)
    || (raw.remaining !== null && remaining === null) || error === undefined || !failures || failures.some(f => f === null)) return null
  const [embedded, failed, gone, payloads, passes, verified, repaired] = numbers as number[]
  return {
    modelId, state, startedAt, finishedAt, verified, repaired, embedded, failed, gone, payloads, passes, remaining, error,
    failures: failures as BuildFailure[],
  }
}

/** GET /api/embedding-models, or null when any part does not read cleanly. */
export function toModelOverview(value: unknown): ModelOverview | null {
  const raw = untrusted<EmbeddingModelsResponseWire>(value)
  if (raw?.ok !== true || !Array.isArray(raw.models)) return null
  const images = count(raw.images)
  const configured = toConfigured(raw.configured)
  const retrieval = toRetrieval(raw.retrieval)
  const build = raw.build === null ? null : toModelBuild(raw.build)
  const switchCatchUpMax = count(raw.switch_catch_up_max)
  const models = raw.models.map(toEmbeddingModel)
  if (images === null || !configured || !retrieval || (raw.build !== null && !build) || switchCatchUpMax === null
    || models.some(m => m === null)) return null
  const list = models as EmbeddingModel[]
  const ids = new Set(list.map(m => m.id))
  const current = list.filter(m => m.status === 'current')
  const known = (id: number | null) => id === null || ids.has(id)
  if (ids.size !== list.length || current.length > 1 || !known(configured.modelId) || !known(retrieval.modelId)
    || (build !== null && !ids.has(build.modelId)) || list.some(m => m.embedded > images)) return null
  return { images, configured, retrieval, build, switchCatchUpMax, models: list }
}

/** The ledger lists more vectors than the collection really holds (some were lost). */
export function lostPoints(model: EmbeddingModel): boolean {
  return model.status !== 'retired' && model.points !== null && model.points < model.embedded
}

/** Its encoder can be had: in memory, or loaded again from weights still in the image. */
export function encoderUsable(model: EmbeddingModel): boolean {
  if (model.encoder === 'configured' || model.encoder === 'serving') return true
  return model.encoder === 'recorded' && model.weights !== 'missing' && model.weights !== 'changed'
}

/** The first 12 hex characters: the embeddingcls-<hash> folder name. */
export function shortArtifact(artifact: string | null): string | null {
  return artifact ? artifact.slice(0, 12) : null
}

/** A build that is still going (the page keeps polling). */
export function buildActive(build: ModelBuild | null): boolean {
  return build !== null && (build.state === 'running' || build.state === 'stopping')
}
