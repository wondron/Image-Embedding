import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRule, deleteSceneResource, readMemberships, updateRule, writeLabel, writeMembership, writeScene } from '.'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'X-Trace-Id': 'trace-1' } })
const scene = (patch: Record<string, unknown> = {}) => ({
  id: 7, code: 'steam_oven', name: '蒸烤箱', description: null, include_all: false, outputs_heating: false, is_active: true,
  created_at: null, member_count: 0, labels: [], rules: [], ...patch,
})
const IMAGE = '0cf457e2-4a47-9f02-fd4d-34540389f720'
const snapshot = (extra: Record<string, unknown> = {}) => ({
  ok: true, image_id: IMAGE, payload_version: 3, scenes: ['general', 'oven'],
  memberships: [
    { scene_id: 1, scene_code: 'general', is_active: true, include_all: true, is_member: true, origin: 'implicit', manual_override: null },
    { scene_id: 2, scene_code: 'oven', is_active: true, include_all: false, is_member: true, origin: 'manual', manual_override: true },
  ],
  ...extra,
})

function stubFetch(...responses: Response[]) {
  const fetchMock = vi.fn()
  for (const response of responses) fetchMock.mockResolvedValueOnce(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
const body = (fetchMock: ReturnType<typeof vi.fn>, call = 0) => JSON.parse(fetchMock.mock.calls[call][1].body as string)

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('scene configuration writes', () => {
  it('changes a rule target and label in one PATCH; a cuisine rule never sends a label', async () => {
    const fetchMock = stubFetch(json({ ok: true, scene: scene(), sync_required: true }), json({ ok: true, scene: scene(), sync_required: true }))
    const first = await updateRule('', 7, 4, { categoryId: 12, cuisine: null, labelId: 3 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/scenes/7/rules/4')
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH')
    expect(body(fetchMock)).toEqual({ category_id: 12, cuisine: null, label_id: 3 })
    expect(first).toMatchObject({ syncRequired: true, deletedId: null, traceId: 'trace-1', scene: { id: 7, code: 'steam_oven' } })
    await updateRule('', 7, 4, { categoryId: null, cuisine: ' chinese ', labelId: 3 })
    expect(body(fetchMock, 1)).toEqual({ category_id: null, cuisine: 'chinese', label_id: null })
    expect(fetchMock).toHaveBeenCalledTimes(2) // no delete-then-create any more
  })

  it('creates a rule with the same body', async () => {
    const fetchMock = stubFetch(json({ ok: true, scene: scene(), sync_required: true }, 201))
    await createRule('', 7, { categoryId: 12, cuisine: null, labelId: null })
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(body(fetchMock)).toEqual({ category_id: 12, cuisine: null, label_id: null })
  })

  it('reads sync_required; an older server that does not send it always needed the sync', async () => {
    stubFetch(
      json({ ok: true, scene: scene(), sync_required: false }),
      json({ ok: true, scene: scene() }),
      json({ ok: true, deleted: { id: 7, code: 'steam_oven' }, sync_required: false }),
      json({ ok: true, scene: scene(), sync_required: 'no' }),
    )
    expect((await writeLabel('', 7, { code: 'steak', name: '牛排', sortOrder: 0, isActive: false }, 3)).syncRequired).toBe(false)
    expect((await writeScene('', { code: 'steam_oven', name: '蒸烤箱', description: null, includeAll: false, outputsHeating: true, isActive: true }, 7)).syncRequired).toBe(true)
    expect(await deleteSceneResource('', 7)).toEqual({ scene: null, deletedId: 7, syncRequired: false, traceId: 'trace-1' })
    await expect(writeScene('', { code: 'x', name: 'x', description: null, includeAll: false, outputsHeating: false, isActive: true })).rejects.toMatchObject({ code: 'SCENE_PROTOCOL_ERROR' })
  })
})

describe('image scene memberships', () => {
  it('reads the snapshot with GET', async () => {
    const fetchMock = stubFetch(json(snapshot()))
    const value = await readMemberships('http://127.0.0.1:8320', IMAGE)
    expect(fetchMock.mock.calls[0][0]).toBe(`http://127.0.0.1:8320/api/images/${IMAGE}/scene-memberships`)
    expect(fetchMock.mock.calls[0][1].method).toBe('GET')
    expect(value.memberships[1]).toEqual({ sceneId: 2, sceneCode: 'oven', isActive: true, includeAll: false, isMember: true, origin: 'manual', manualOverride: true })
  })

  it('includes / excludes with PUT and goes back to the rules with DELETE', async () => {
    const fetchMock = stubFetch(
      json(snapshot({ vector_sync: 'pushed', sync_required: false })),
      json(snapshot({ vector_sync: 'not_ready', sync_required: true })),
    )
    const pushed = await writeMembership('', IMAGE, 2, false)
    expect(fetchMock.mock.calls[0][1].method).toBe('PUT')
    expect(body(fetchMock)).toEqual({ is_member: false })
    expect(pushed).toMatchObject({ vectorSync: 'pushed', syncRequired: false, payloadVersion: 3 })
    const cleared = await writeMembership('', IMAGE, 2, null)
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/images/${IMAGE}/scene-memberships/2`)
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'DELETE' })
    expect(fetchMock.mock.calls[1][1].body).toBeUndefined()
    expect(cleared).toMatchObject({ vectorSync: 'not_ready', syncRequired: true })
  })

  it('accepts an older server without vector_sync, rejects anything off-contract', async () => {
    stubFetch(
      json(snapshot({ sync_required: true })),
      json(snapshot({ vector_sync: 'queued', sync_required: true })),
      json(snapshot({ vector_sync: 'pushed' })),
      json(snapshot({ memberships: [{ scene_id: 2, scene_code: 'oven', is_active: true, include_all: false, is_member: true, origin: 'auto', manual_override: null }] })),
    )
    expect(await writeMembership('', IMAGE, 2, true)).toMatchObject({ vectorSync: null, syncRequired: true })
    await expect(writeMembership('', IMAGE, 2, true)).rejects.toMatchObject({ code: 'SCENE_PROTOCOL_ERROR' })
    await expect(writeMembership('', IMAGE, 2, true)).rejects.toMatchObject({ code: 'SCENE_PROTOCOL_ERROR' })
    await expect(readMemberships('', IMAGE)).rejects.toMatchObject({ code: 'SCENE_PROTOCOL_ERROR' })
  })
})
