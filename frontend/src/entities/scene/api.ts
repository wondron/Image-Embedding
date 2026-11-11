import { ApiError, requestJsonResult, type RequestJsonOptions } from '../../shared/api'
import type {
  LabelInput, Membership, MembershipSnapshot, MembershipWrite, RuleInput, Scene, SceneCatalog, SceneInput, SceneWrite, SyncBatch, VectorSync,
} from './types'

const labels = { service: '场景服务', endpoint: '场景接口', action: '场景操作' }
type Obj = Record<string, unknown>

// Strict readers: anything off-contract throws, and call() turns that into SCENE_PROTOCOL_ERROR.
function obj(v: unknown): Obj { if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('对象缺失'); return v as Obj }
function str(v: unknown): string { if (typeof v !== 'string') throw new Error('字符串缺失'); return v }
function bool(v: unknown): boolean { if (typeof v !== 'boolean') throw new Error('布尔字段缺失'); return v }
function num(v: unknown): number { if (typeof v !== 'number' || !Number.isSafeInteger(v)) throw new Error('整数字段缺失'); return v }
function list(v: unknown): unknown[] { if (!Array.isArray(v)) throw new Error('数组字段缺失'); return v }
function nullable<T>(v: unknown, parse: (v: unknown) => T): T | null { return v === null ? null : parse(v) }
/** A field newer servers send: absent from an older build means the old behaviour (fallback). */
function added<T>(v: unknown, parse: (v: unknown) => T, fallback: T): T { return v === undefined ? fallback : parse(v) }
function ref(v: unknown) { const o = obj(v); return { id: num(o.id), code: str(o.code), name: nullable(o.name, str) } }

function scene(v: unknown): Scene {
  const o = obj(v)
  return {
    id: num(o.id), code: str(o.code), name: str(o.name), description: nullable(o.description, str),
    includeAll: bool(o.include_all), outputsHeating: bool(o.outputs_heating), isActive: bool(o.is_active),
    createdAt: nullable(o.created_at, str), memberCount: num(o.member_count),
    labels: list(o.labels).map(v => { const l = obj(v); return { id: num(l.id), code: str(l.code), name: str(l.name), sortOrder: num(l.sort_order), isActive: bool(l.is_active), ruleCount: num(l.rule_count) } }),
    rules: list(o.rules).map(v => { const r = obj(v); return { id: num(r.id), category: nullable(r.category, ref), cuisine: nullable(r.cuisine, str), label: nullable(r.label, ref) } }),
  }
}

async function call<T>(path: string, baseUrl: string, options: Partial<RequestJsonOptions>, parse: (o: Obj, trace: string | null) => T): Promise<T> {
  const r = await requestJsonResult(path, { baseUrl, labels, signal: AbortSignal.timeout(120000), ...options })
  try { const o = obj(r.body); if (o.ok !== true) throw new Error('ok缺失'); return parse(o, r.traceId) }
  catch { throw new ApiError('场景接口响应不符合契约，请刷新对账后再操作。', { status: r.status, traceId: r.traceId, code: 'SCENE_PROTOCOL_ERROR' }) }
}

/** Older servers did not say; they always needed the sync. */
function syncRequired(o: Obj): boolean { return added(o.sync_required, bool, true) }
function sceneWrite(o: Obj, traceId: string | null): SceneWrite { return { scene: scene(o.scene), deletedId: null, syncRequired: syncRequired(o), traceId } }

export function listScenes(baseUrl = ''): Promise<SceneCatalog> { return call('/api/scenes', baseUrl, {}, (o, traceId) => ({ scenes: list(o.scenes).map(scene), defaultScene: str(o.default_scene), traceId })) }

export function writeScene(baseUrl: string, input: SceneInput, id?: number): Promise<SceneWrite> {
  const json = { name: input.name.trim(), description: input.description?.trim() || null, include_all: input.includeAll, outputs_heating: input.outputsHeating, is_active: input.isActive, ...(id === undefined ? { code: input.code.trim() } : {}) }
  return call(id === undefined ? '/api/scenes' : `/api/scenes/${id}`, baseUrl, { method: id === undefined ? 'POST' : 'PATCH', json }, sceneWrite)
}

export function writeLabel(baseUrl: string, sceneId: number, input: LabelInput, id?: number): Promise<SceneWrite> {
  const json = { code: input.code.trim(), name: input.name.trim(), sort_order: input.sortOrder, is_active: input.isActive }
  return call(`/api/scenes/${sceneId}/labels${id === undefined ? '' : `/${id}`}`, baseUrl, { method: id === undefined ? 'POST' : 'PATCH', json }, sceneWrite)
}

function ruleBody(input: RuleInput) {
  const cuisine = input.cuisine?.trim() || null
  return { category_id: input.categoryId, cuisine, label_id: cuisine === null ? input.labelId : null }
}

export function createRule(baseUrl: string, sceneId: number, input: RuleInput): Promise<SceneWrite> {
  return call(`/api/scenes/${sceneId}/rules`, baseUrl, { method: 'POST', json: ruleBody(input) }, sceneWrite)
}

/** One PATCH: a new target and / or label, in place on the server (the rule keeps its id). */
export function updateRule(baseUrl: string, sceneId: number, ruleId: number, input: RuleInput): Promise<SceneWrite> {
  return call(`/api/scenes/${sceneId}/rules/${ruleId}`, baseUrl, { method: 'PATCH', json: ruleBody(input) }, sceneWrite)
}

export function deleteSceneResource(baseUrl: string, sceneId: number, kind?: 'labels' | 'rules', id?: number): Promise<SceneWrite> {
  return call(`/api/scenes/${sceneId}${kind ? `/${kind}/${id}` : ''}`, baseUrl, { method: 'DELETE' }, (o, traceId) => kind
    ? sceneWrite(o, traceId)
    : { scene: null, deletedId: num(obj(o.deleted).id), syncRequired: syncRequired(o), traceId })
}

export function syncScenes(baseUrl: string, after: string | null, limit: number): Promise<SyncBatch> { return call('/api/scenes/sync', baseUrl, { method: 'POST', json: { after, limit } }, (o, traceId) => ({ processed: num(o.processed), membershipChanged: num(o.membership_changed), payloadPushed: num(o.payload_pushed), payloadMissing: list(o.payload_missing).map(str), nextAfter: nullable(o.next_after, str), done: bool(o.done), traceId })) }

const ORIGINS: readonly (Membership['origin'])[] = [null, 'implicit', 'rule', 'manual']
const VECTOR_SYNC: readonly VectorSync[] = ['pushed', 'missing', 'not_ready', 'failed']
function vectorSync(v: unknown): VectorSync { if (!VECTOR_SYNC.includes(v as VectorSync)) throw new Error('vector_sync非法'); return v as VectorSync }
function membershipSnapshot(o: Obj, traceId: string | null): MembershipSnapshot {
  return {
    imageId: str(o.image_id), payloadVersion: num(o.payload_version), scenes: list(o.scenes).map(str), traceId,
    memberships: list(o.memberships).map(v => {
      const m = obj(v)
      if (!ORIGINS.includes(m.origin as Membership['origin'])) throw new Error('origin非法')
      return { sceneId: num(m.scene_id), sceneCode: str(m.scene_code), isActive: bool(m.is_active), includeAll: bool(m.include_all), isMember: bool(m.is_member), origin: m.origin as Membership['origin'], manualOverride: nullable(m.manual_override, bool) }
    }),
  }
}
const membershipPath = (imageId: string) => `/api/images/${encodeURIComponent(imageId)}/scene-memberships`

/** The image's stored membership in every scene (no rule preview, no vector state). */
export function readMemberships(baseUrl: string, imageId: string): Promise<MembershipSnapshot> {
  return call(membershipPath(imageId), baseUrl, {}, membershipSnapshot)
}

/** isMember true / false: manual include / exclude (PUT); null: back to the rules (DELETE). */
export function writeMembership(baseUrl: string, imageId: string, sceneId: number, isMember: boolean | null): Promise<MembershipWrite> {
  const options: Partial<RequestJsonOptions> = isMember === null ? { method: 'DELETE' } : { method: 'PUT', json: { is_member: isMember } }
  return call(`${membershipPath(imageId)}/${sceneId}`, baseUrl, options, (o, traceId) => ({
    ...membershipSnapshot(o, traceId), syncRequired: bool(o.sync_required), vectorSync: added(o.vector_sync, vectorSync, null),
  }))
}

export function sceneError(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : '场景操作失败'
  const protection = error.code === 'SCENE_PROTECTED' ? '默认场景不可停用、删除或取消全包含。' : ''
  return `${protection}${error.message}${error.errors ? ` · ${JSON.stringify(error.errors)}` : ''}${error.traceId ? ` · trace_id=${error.traceId}` : ''}`
}
export function uncertainWrite(error: unknown): boolean { return !(error instanceof ApiError) || error.status === null || error.code === 'SCENE_PROTOCOL_ERROR' || (error.status !== null && error.status >= 500) }
export function unknownScene(error: unknown): boolean { return error instanceof ApiError && ['SEARCH_UNKNOWN_SCENE', 'LIST_UNKNOWN_SCENE', 'INDEX_UNKNOWN_SCENE'].includes(error.code ?? '') }
