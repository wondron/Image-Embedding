import { asBoolean, asInteger, asText, untrusted, untrustedList } from '../../shared/lib/parse'
import type { ImportChangeWire, ImportLeftoverWire, ImportResponseWire, ImportRowWire, ImportSummaryWire } from '../../shared/api'
import { toCategorySceneSync, type CategorySceneSync } from '../../entities/category'

export type ImportRowKind = 'category' | 'scene' | 'label' | 'rule'
export type ImportAction = 'create' | 'update' | 'delete' | 'error'

export interface ImportChange {
  field: string
  before: string | null
  after: string | null
}

/** One item of the file that was (in a preview: would be) created, updated or deleted, or that is not imported. */
export interface ImportRow {
  kind: ImportRowKind
  /** Category code; scene code; 「scene/label」; 「scene/category:code」 or 「scene/cuisine:name」. */
  key: string
  action: ImportAction
  name: string | null
  scene: string | null
  /** Its place in the file, e.g. categories[0].children[2]. */
  where: string | null
  changes: ImportChange[]
  error: string | null
  detail: string | null
}

export interface ImportSummary {
  /** Items the file lists. */
  total: number
  create: number
  update: number
  delete: number
  /** Already as stored: counted, not listed in rows. */
  unchanged: number
  error: number
  /** Stored, but not listed in the file (an import leaves them alone). */
  notInFile: number
}

export interface ImportLeftover {
  id: number
  code: string
  name: string | null
}

/** What POST /api/categories/import and POST /api/scenes/import answer (contract §6.8). */
export interface ImportReport {
  /** true: a preview, nothing was written. */
  dryRun: boolean
  summary: ImportSummary
  rows: ImportRow[]
  /** rows was cut at the server's limit; summary still counts everything. */
  rowsTruncated: boolean
  notInFile: ImportLeftover[]
  /** Categories: a cuisines change recomputed the scenes of stored images (null otherwise). */
  sceneSync: CategorySceneSync | null
  /** Scenes: whether rules and labels the file does not list were deleted. */
  prune: boolean
  /** Scenes: stored images follow the new configuration only after the full sync. */
  syncRequired: boolean
  traceId: string | null
}

const KINDS: readonly ImportRowKind[] = ['category', 'scene', 'label', 'rule']
const ACTIONS: readonly ImportAction[] = ['create', 'update', 'delete', 'error']

function count(value: unknown): number {
  const number = asInteger(value)
  return number !== null && number > 0 ? number : 0
}

function toRow(value: unknown): ImportRow | null {
  const row = untrusted<ImportRowWire>(value)
  const kind = KINDS.find(known => known === row?.kind)
  const action = ACTIONS.find(known => known === row?.action)
  const key = asText(row?.key)
  if (!row || !kind || !action || !key) return null
  return {
    kind,
    key,
    action,
    name: asText(row.name),
    scene: asText(row.scene),
    where: asText(row.where),
    changes: untrustedList<ImportChangeWire>(row.changes).flatMap(change => {
      const field = asText(change.field)
      return field ? [{ field, before: asText(change.before), after: asText(change.after) }] : []
    }),
    error: asText(row.error),
    detail: asText(row.detail),
  }
}

/** The report of an import body; null when the body is not the contract's shape. */
export function toImportReport(payload: unknown, traceId: string | null): ImportReport | null {
  const body = untrusted<ImportResponseWire>(payload)
  const summary = untrusted<ImportSummaryWire>(body?.summary)
  const dryRun = asBoolean(body?.dry_run)
  if (!body || body.ok !== true || !summary || dryRun === undefined || !Array.isArray(body.rows)) return null
  return {
    dryRun,
    summary: {
      total: count(summary.total),
      create: count(summary.create),
      update: count(summary.update),
      delete: count(summary.delete),
      unchanged: count(summary.unchanged),
      error: count(summary.error),
      notInFile: count(summary.not_in_file),
    },
    rows: body.rows.flatMap(entry => {
      const row = toRow(entry)
      return row ? [row] : []
    }),
    rowsTruncated: asBoolean(body.rows_truncated) === true,
    notInFile: untrustedList<ImportLeftoverWire>(body.not_in_file).flatMap(entry => {
      const id = asInteger(entry.id)
      const code = asText(entry.code)
      return id !== null && code ? [{ id, code, name: asText(entry.name) }] : []
    }),
    sceneSync: toCategorySceneSync(payload),
    prune: asBoolean(body.prune) === true,
    syncRequired: asBoolean(body.sync_required) === true,
    traceId,
  }
}

/** Items a real import would write (or wrote). */
export function importChangeCount(summary: ImportSummary): number {
  return summary.create + summary.update + summary.delete
}

export const IMPORT_KIND_TEXT: Readonly<Record<ImportRowKind, string>> = {
  category: '类别', scene: '场景', label: '场景标签', rule: '规则',
}

export const IMPORT_ACTION_TEXT: Readonly<Record<ImportAction, string>> = {
  create: '新建', update: '更新', delete: '删除', error: '有问题',
}

/** What the server calls a changed field → what the pages call it. */
const FIELD_TEXT: Readonly<Record<string, string>> = {
  names: '名称',
  name: '名称',
  parent: '上级类别',
  cuisines: '菜系',
  heating_group: '加热组',
  sort_order: '排序',
  is_active: '启用',
  description: '描述',
  include_all: '包含全部图片',
  outputs_heating: '输出加热信息',
  label: '对应的场景标签',
}

const VALUE_TEXT: Readonly<Record<string, string>> = { true: '是', false: '否' }

/** One change as a line: 「排序：0 → 5」; a new value alone for something created. */
export function importChangeText(change: ImportChange): string {
  const field = FIELD_TEXT[change.field] ?? change.field
  const show = (value: string | null) => (value === null ? '（空）' : VALUE_TEXT[value] ?? value)
  return change.before === null ? `${field}：${show(change.after)}` : `${field}：${show(change.before)} → ${show(change.after)}`
}
