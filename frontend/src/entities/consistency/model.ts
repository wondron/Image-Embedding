/**
 * 数据核对 (backend app/schemas/consistency.py): whether PostgreSQL, Qdrant and the object storage
 * agree, and whether the live model versions agree with each other. The report is read field by
 * field; a run whose core does not read cleanly is unreadable as a whole (the page says so instead
 * of showing half a report as if it were all of it).
 */
import { asBoolean, asInteger, asText, untrusted } from '../../shared/lib/parse'
import type {
  ConsistencyConnectionsWire,
  ConsistencyDatabaseWire,
  ConsistencyFilesWire,
  ConsistencyFindingWire,
  ConsistencyIssueWire,
  ConsistencyOptionsWire,
  ConsistencyProgressWire,
  ConsistencyReportWire,
  ConsistencyResponseWire,
  ConsistencyRunWire,
  ConsistencyVersionWire,
} from '../../shared/api'

export type CheckState = 'running' | 'stopping' | 'done' | 'stopped' | 'failed'
export type IssueLevel = 'error' | 'warn' | 'info'
export type ReportStatus = 'ok' | 'warn' | 'error'
export type EncoderCheckStatus = 'ok' | 'mismatch' | 'unchecked' | 'not_loaded' | 'off'

export const VERSION_FINDINGS = ['missing', 'lost', 'unrecorded', 'ledger_behind', 'stale_payload', 'orphan_points', 'pending_points'] as const
export type VersionFindingKey = typeof VERSION_FINDINGS[number]
export const FILE_FINDINGS = ['missing_original', 'missing_thumb', 'unreadable', 'content_mismatch', 'orphan_files', 'pending_files'] as const
export type FileFindingKey = typeof FILE_FINDINGS[number]

const CHECK_STATES: readonly CheckState[] = ['running', 'stopping', 'done', 'stopped', 'failed']
const LEVELS: readonly IssueLevel[] = ['error', 'warn', 'info']
const STATUSES: readonly ReportStatus[] = ['ok', 'warn', 'error']
const ENCODER_STATUSES: readonly EncoderCheckStatus[] = ['ok', 'mismatch', 'unchecked', 'not_loaded', 'off']

export interface Finding {
  count: number
  /** The first 20: image ids, point ids or object keys. */
  samples: string[]
}

export interface CheckOptions {
  /** PostgreSQL ↔ Qdrant, every live version. */
  vectors: boolean
  /** PostgreSQL → object storage: each image's original and thumbnail (HEAD, no download). */
  files: boolean
  /** The other way: points and objects nothing in the database refers to. */
  orphans: boolean
  /** Download every original and compare its SHA-256 (slow; needs files). */
  content: boolean
  /** Embed a few stored images again with a loaded encoder: did this version's encoder write its collection? */
  encoders: boolean
}

export const DEFAULT_CHECK_OPTIONS: CheckOptions = { vectors: true, files: true, orphans: true, content: false, encoders: true }

export interface CheckProgress {
  phase: string
  done: number
  total: number | null
  label: string | null
}

export interface Connections {
  postgres: { ok: boolean; host: string | null; database: string | null; detail: string | null }
  qdrant: { ok: boolean; url: string | null; configuredUrl: string | null; restartRequired: boolean; collections: number | null; detail: string | null }
  storage: { ok: boolean; driver: string | null; bucket: string | null; endpointUrl: string | null; publicBaseUrl: string | null; keyPrefix: string | null; detail: string | null }
  serving: { modelId: number | null; label: string | null; detail: string | null }
}

export interface DatabaseCounts {
  images: number
  pendingDeletions: number
  oldestDeletionAt: string | null
  currentModels: number
}

export interface VersionReport {
  id: number
  label: string
  status: string
  collection: string
  dim: number
  distance: string
  serving: boolean
  weights: 'ok' | 'missing' | 'changed' | null
  qdrant: { exists: boolean | null; dim: number | null; distance: string | null; points: number | null; matches: boolean | null }
  error: string | null
  scanned: number
  ledger: number
  withVector: number
  findings: Record<VersionFindingKey, Finding>
  vsCurrent: { onlyCurrent: Finding; onlyThis: Finding } | null
  encoder: { status: EncoderCheckStatus; source: 'configured' | 'serving' | null; checked: number; minSimilarity: number | null; detail: string | null }
}

export interface FilesReport {
  skipped: string | null
  checked: number
  firstError: string | null
  listed: number | null
  referenced: number | null
  truncated: boolean
  listError: string | null
  findings: Record<FileFindingKey, Finding>
}

export interface Issue {
  level: IssueLevel
  code: string
  count: number | null
  versionId: number | null
  label: string | null
  detail: string | null
}

export interface StrayCollection {
  name: string
  modelId: number | null
  label: string | null
  status: string | null
}

export interface ConsistencyReport {
  status: ReportStatus
  /** false: stopped part way; only part was checked. */
  complete: boolean
  options: CheckOptions
  durationMs: number
  connections: Connections
  database: DatabaseCounts
  versions: VersionReport[]
  strayCollections: StrayCollection[] | null
  strayError: string | null
  files: FilesReport | null
  /** Worst first. */
  issues: Issue[]
}

export interface ConsistencyRun {
  state: CheckState
  startedAt: string
  finishedAt: string | null
  options: CheckOptions
  progress: CheckProgress
  error: string | null
  report: ConsistencyReport | null
}

const count = (value: unknown): number | null => {
  const n = asInteger(value)
  return n !== null && n >= 0 ? n : null
}
/** null stays null, a string is kept as it is, anything else is unreadable (undefined). */
const nullableText = (value: unknown): string | null | undefined =>
  value === null || value === undefined ? null : typeof value === 'string' ? value : undefined
const nullableCount = (value: unknown): number | null | undefined =>
  value === null || value === undefined ? null : count(value) ?? undefined
const nullableBool = (value: unknown): boolean | null | undefined =>
  value === null || value === undefined ? null : asBoolean(value)

function oneOf<T extends string>(values: readonly T[], value: unknown): T | null {
  return typeof value === 'string' && (values as readonly string[]).includes(value) ? value as T : null
}

function toFinding(value: unknown): Finding | null {
  const raw = untrusted<ConsistencyFindingWire>(value)
  const n = count(raw?.count)
  if (!raw || n === null || !Array.isArray(raw.samples)) return null
  const samples = raw.samples.filter((entry): entry is string => typeof entry === 'string')
  return { count: n, samples }
}

function toFindings<K extends string>(keys: readonly K[], value: unknown): Record<K, Finding> | null {
  const raw = untrusted<Record<K, unknown>>(value)
  if (!raw) return null
  const out = {} as Record<K, Finding>
  for (const key of keys) {
    const finding = toFinding((raw as Record<string, unknown>)[key])
    if (!finding) return null
    out[key] = finding
  }
  return out
}

function toOptions(value: unknown): CheckOptions | null {
  const raw = untrusted<ConsistencyOptionsWire>(value)
  const flags = [raw?.vectors, raw?.files, raw?.orphans, raw?.content, raw?.encoders].map(asBoolean)
  if (!raw || flags.some(flag => flag === undefined)) return null
  const [vectors, files, orphans, content, encoders] = flags as boolean[]
  return { vectors, files, orphans, content, encoders }
}

function toProgress(value: unknown): CheckProgress | null {
  const raw = untrusted<ConsistencyProgressWire>(value)
  const phase = asText(raw?.phase)
  const done = count(raw?.done)
  const total = nullableCount(raw?.total)
  const label = nullableText(raw?.label)
  if (!phase || done === null || total === undefined || label === undefined) return null
  return { phase, done, total, label }
}

function toConnections(value: unknown): Connections | null {
  const raw = untrusted<ConsistencyConnectionsWire>(value)
  const pg = untrusted<ConsistencyConnectionsWire['postgres']>(raw?.postgres)
  const qd = untrusted<ConsistencyConnectionsWire['qdrant']>(raw?.qdrant)
  const st = untrusted<ConsistencyConnectionsWire['storage']>(raw?.storage)
  const sv = untrusted<ConsistencyConnectionsWire['serving']>(raw?.serving)
  if (!pg || !qd || !st || !sv) return null
  const texts = [pg.host, pg.database, pg.detail, qd.url, qd.configured_url, qd.detail, st.driver, st.bucket, st.endpoint_url,
    st.public_base_url, st.key_prefix, st.detail, sv.label, sv.detail].map(nullableText)
  const flags = [pg.ok, qd.ok, qd.restart_required, st.ok].map(asBoolean)
  const collections = nullableCount(qd.collections)
  const modelId = nullableCount(sv.model_id)
  if (texts.some(t => t === undefined) || flags.some(f => f === undefined) || collections === undefined || modelId === undefined) return null
  const [host, database, pgDetail, url, configuredUrl, qdDetail, driver, bucket, endpointUrl, publicBaseUrl, keyPrefix, stDetail, label, svDetail] = texts as (string | null)[]
  const [pgOk, qdOk, restartRequired, stOk] = flags as boolean[]
  return {
    postgres: { ok: pgOk, host, database, detail: pgDetail },
    qdrant: { ok: qdOk, url, configuredUrl, restartRequired, collections, detail: qdDetail },
    storage: { ok: stOk, driver, bucket, endpointUrl, publicBaseUrl, keyPrefix, detail: stDetail },
    serving: { modelId, label, detail: svDetail },
  }
}

function toDatabase(value: unknown): DatabaseCounts | null {
  const raw = untrusted<ConsistencyDatabaseWire>(value)
  const images = count(raw?.images)
  const pendingDeletions = count(raw?.pending_deletions)
  const currentModels = count(raw?.current_models)
  const oldestDeletionAt = nullableText(raw?.oldest_deletion_at)
  if (images === null || pendingDeletions === null || currentModels === null || oldestDeletionAt === undefined) return null
  return { images, pendingDeletions, oldestDeletionAt, currentModels }
}

function toVersion(value: unknown): VersionReport | null {
  const raw = untrusted<ConsistencyVersionWire>(value)
  if (!raw) return null
  const id = count(raw.id)
  const label = asText(raw.label)
  const status = asText(raw.status)
  const collection = asText(raw.collection)
  const dim = count(raw.dim)
  const distance = asText(raw.distance)
  const serving = asBoolean(raw.serving)
  const weights = raw.weights === null || raw.weights === undefined ? null : oneOf(['ok', 'missing', 'changed'] as const, raw.weights)
  const q = untrusted<ConsistencyVersionWire['qdrant']>(raw.qdrant)
  const exists = nullableBool(q?.exists)
  const qDim = nullableCount(q?.dim)
  const qDistance = nullableText(q?.distance)
  const points = nullableCount(q?.points)
  const matches = nullableBool(q?.matches)
  const error = nullableText(raw.error)
  const [scanned, ledger, withVector] = [raw.scanned, raw.ledger, raw.with_vector].map(count)
  const findings = toFindings(VERSION_FINDINGS, raw.findings)
  const vs = raw.vs_current === null || raw.vs_current === undefined ? null : untrusted<NonNullable<ConsistencyVersionWire['vs_current']>>(raw.vs_current)
  const onlyCurrent = vs ? toFinding(vs.only_current) : null
  const onlyThis = vs ? toFinding(vs.only_this) : null
  const e = untrusted<ConsistencyVersionWire['encoder']>(raw.encoder)
  const encoderStatus = oneOf(ENCODER_STATUSES, e?.status)
  const source = e?.source === null || e?.source === undefined ? null : oneOf(['configured', 'serving'] as const, e.source)
  const checked = count(e?.checked)
  const minSimilarity = e?.min_similarity === null || e?.min_similarity === undefined ? null
    : typeof e.min_similarity === 'number' && Number.isFinite(e.min_similarity) ? e.min_similarity : undefined
  const encoderDetail = nullableText(e?.detail)
  if (id === null || !label || !status || !collection || dim === null || !distance || serving === undefined
    || (raw.weights !== null && raw.weights !== undefined && weights === null) || !q || exists === undefined || qDim === undefined
    || qDistance === undefined || points === undefined || matches === undefined || error === undefined
    || scanned === null || ledger === null || withVector === null || !findings || (vs && (!onlyCurrent || !onlyThis))
    || !e || !encoderStatus || (e.source !== null && e.source !== undefined && source === null) || checked === null
    || minSimilarity === undefined || encoderDetail === undefined) return null
  return {
    id, label, status, collection, dim, distance, serving, weights,
    qdrant: { exists, dim: qDim, distance: qDistance, points, matches },
    error, scanned, ledger, withVector, findings,
    vsCurrent: vs ? { onlyCurrent: onlyCurrent!, onlyThis: onlyThis! } : null,
    encoder: { status: encoderStatus, source, checked, minSimilarity, detail: encoderDetail },
  }
}

function toFiles(value: unknown): FilesReport | null {
  const raw = untrusted<ConsistencyFilesWire>(value)
  if (!raw) return null
  const skipped = nullableText(raw.skipped)
  const checked = count(raw.checked)
  const firstError = nullableText(raw.first_error)
  const listed = nullableCount(raw.listed)
  const referenced = nullableCount(raw.referenced)
  const truncated = asBoolean(raw.truncated)
  const listError = nullableText(raw.list_error)
  const findings = toFindings(FILE_FINDINGS, raw.findings)
  if (skipped === undefined || checked === null || firstError === undefined || listed === undefined || referenced === undefined
    || truncated === undefined || listError === undefined || !findings) return null
  return { skipped, checked, firstError, listed, referenced, truncated, listError, findings }
}

function toIssue(value: unknown): Issue | null {
  const raw = untrusted<ConsistencyIssueWire>(value)
  const level = oneOf(LEVELS, raw?.level)
  const code = asText(raw?.code)
  const n = nullableCount(raw?.count)
  const versionId = nullableCount(raw?.version_id)
  const label = nullableText(raw?.label)
  const detail = nullableText(raw?.detail)
  if (!level || !code || n === undefined || versionId === undefined || label === undefined || detail === undefined) return null
  return { level, code, count: n, versionId, label, detail }
}

function toStray(value: unknown): StrayCollection | null {
  const raw = untrusted<{ name: string; model_id: number | null; label: string | null; status: string | null }>(value)
  const name = asText(raw?.name)
  const modelId = nullableCount(raw?.model_id)
  const label = nullableText(raw?.label)
  const status = nullableText(raw?.status)
  if (!name || modelId === undefined || label === undefined || status === undefined) return null
  return { name, modelId, label, status }
}

export function toConsistencyReport(value: unknown): ConsistencyReport | null {
  const raw = untrusted<ConsistencyReportWire>(value)
  if (!raw) return null
  const status = oneOf(STATUSES, raw.status)
  const complete = asBoolean(raw.complete)
  const options = toOptions(raw.options)
  const durationMs = count(raw.duration_ms)
  const connections = toConnections(raw.connections)
  const database = toDatabase(raw.database)
  const versions = Array.isArray(raw.versions) ? raw.versions.map(toVersion) : null
  const stray = raw.stray_collections === null || raw.stray_collections === undefined ? null
    : Array.isArray(raw.stray_collections) ? raw.stray_collections.map(toStray) : undefined
  const strayError = nullableText(raw.stray_error)
  const files = raw.files === null || raw.files === undefined ? null : toFiles(raw.files)
  const issues = Array.isArray(raw.issues) ? raw.issues.map(toIssue) : null
  if (!status || complete === undefined || !options || durationMs === null || !connections || !database
    || !versions || versions.some(v => v === null) || stray === undefined || (stray && stray.some(s => s === null))
    || strayError === undefined || (raw.files !== null && raw.files !== undefined && !files) || !issues || issues.some(i => i === null)) return null
  return {
    status, complete, options, durationMs, connections, database,
    versions: versions as VersionReport[],
    strayCollections: stray as StrayCollection[] | null,
    strayError, files, issues: issues as Issue[],
  }
}

export function toConsistencyRun(value: unknown): ConsistencyRun | null {
  const raw = untrusted<ConsistencyRunWire>(value)
  if (!raw) return null
  const state = oneOf(CHECK_STATES, raw.state)
  const startedAt = asText(raw.started_at)
  const finishedAt = nullableText(raw.finished_at)
  const options = toOptions(raw.options)
  const progress = toProgress(raw.progress)
  const error = nullableText(raw.error)
  const report = raw.report === null || raw.report === undefined ? null : toConsistencyReport(raw.report)
  if (!state || !startedAt || finishedAt === undefined || !options || !progress || error === undefined
    || (raw.report !== null && raw.report !== undefined && !report)) return null
  return { state, startedAt, finishedAt, options, progress, error, report }
}

/** GET / POST /api/consistency…: { check } (null: nothing checked yet), or undefined when unreadable. */
export function toConsistencyResponse(value: unknown): ConsistencyRun | null | undefined {
  const raw = untrusted<ConsistencyResponseWire>(value)
  if (raw?.ok !== true) return undefined
  if (raw.check === null) return null
  return toConsistencyRun(raw.check) ?? undefined
}

/** The check is still going (the page keeps polling). */
export function checkActive(run: ConsistencyRun | null): boolean {
  return run !== null && (run.state === 'running' || run.state === 'stopping')
}
