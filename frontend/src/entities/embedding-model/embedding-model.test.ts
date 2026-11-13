import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  activateEmbeddingModel,
  buildActive,
  listEmbeddingModels,
  registerEmbeddingModel,
  retireEmbeddingModel,
  startModelBuild,
  stopModelBuild,
  toModelOverview,
} from '.'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

const modelWire = (id: number, fields: Record<string, unknown> = {}) => ({
  id, name: 'dino', version: `v${id}`, status: 'ready', collection: `dino_v${id}`, dim: 1024, distance: 'cosine',
  weights_uri: `config/model/dino/v${id}.onnx`, created_at: '2026-11-01T00:00:00+00:00', activated_at: null,
  embedded: 5, missing: 0, behind: 0, fingerprint: true, configured: false, serving: false, encoder: 'recorded',
  ...fields,
})

const overviewWire = (extra: Record<string, unknown> = {}) => ({
  ok: true,
  images: 5,
  configured: { loaded: true, model_path: 'config/model/dino/v2.onnx', dim: 1024, model_id: 2, detail: null },
  retrieval: { ready: true, model_id: 1, encoder: 'own', code: null, detail: null },
  build: null,
  models: [
    modelWire(1, { status: 'current', serving: true, encoder: 'serving', activated_at: '2026-11-02T00:00:00+00:00' }),
    modelWire(2, { status: 'building', embedded: 2, missing: 3, configured: true, encoder: 'configured' }),
  ],
  ...extra,
})

const buildWire = (fields: Record<string, unknown> = {}) => ({
  model_id: 2, state: 'running', started_at: '2026-11-12T10:00:00+08:00', finished_at: null, embedded: 2, failed: 0,
  gone: 0, payloads: 0, passes: 1, remaining: null, error: null, failures: [], ...fields,
})

afterEach(() => vi.unstubAllGlobals())

describe('toModelOverview', () => {
  it('reads the versions, the configured encoder and what search uses', () => {
    const overview = toModelOverview(overviewWire({ build: buildWire() }))!
    expect(overview.images).toBe(5)
    expect(overview.models.map(m => [m.label, m.status, m.embedded, m.missing, m.encoder])).toEqual([
      ['dino:v1', 'current', 5, 0, 'serving'], ['dino:v2', 'building', 2, 3, 'configured'],
    ])
    expect(overview.configured).toEqual({ loaded: true, modelPath: 'config/model/dino/v2.onnx', dim: 1024, modelId: 2, detail: null })
    expect(overview.retrieval).toEqual({ ready: true, modelId: 1, encoder: 'own', code: null, detail: null })
    expect(overview.build?.state).toBe('running')
    expect(buildActive(overview.build)).toBe(true)
    expect(buildActive(toModelOverview(overviewWire({ build: buildWire({ state: 'done' }) }))!.build)).toBe(false)
  })

  it('reads a stack that could not pair its encoder', () => {
    const overview = toModelOverview(overviewWire({
      retrieval: { ready: false, model_id: null, encoder: null, code: 'EMBEDDING_MODEL_MISMATCH', detail: 'weights gone' },
    }))!
    expect(overview.retrieval).toEqual({ ready: false, modelId: null, encoder: null, code: 'EMBEDDING_MODEL_MISMATCH', detail: 'weights gone' })
  })

  it('refuses an answer that does not read cleanly instead of skipping parts of it', () => {
    const broken = [
      overviewWire({ ok: false }),
      overviewWire({ images: -1 }),
      overviewWire({ models: [modelWire(1, { status: 'archived' })] }),
      overviewWire({ models: [modelWire(1, { embedded: '5' })] }),
      overviewWire({ models: [modelWire(1, { embedded: 6 })] }),  // more vectors than images
      overviewWire({ models: [modelWire(1), modelWire(1)] }),  // the same id twice
      overviewWire({ models: [modelWire(1, { status: 'current' }), modelWire(2, { status: 'current' })] }),
      overviewWire({ configured: { loaded: true, model_path: null, dim: null, model_id: 9, detail: null } }),  // unknown version
      overviewWire({ retrieval: { ready: true, model_id: null, encoder: null, code: null, detail: null } }),
      overviewWire({ build: buildWire({ model_id: 9 }) }),
      overviewWire({ build: buildWire({ failures: [{ image_id: 'a' }] }) }),
    ]
    for (const body of broken) expect(toModelOverview(body)).toBeNull()
  })
})

describe('model version requests', () => {
  it('lists, and refuses a malformed list with a protocol error', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(overviewWire())).mockResolvedValueOnce(json(overviewWire({ images: 'x' })))
    vi.stubGlobal('fetch', fetchMock)
    expect((await listEmbeddingModels({ baseUrl: 'https://api.example' })).models).toHaveLength(2)
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example/api/embedding-models')
    await expect(listEmbeddingModels()).rejects.toMatchObject({ code: 'MODEL_PROTOCOL_ERROR' })
  })

  it('registers with the labels given, or lets the server name it', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json({ ok: true, model: modelWire(3, { status: 'building', embedded: 0, missing: 5 }) }, 201))
    vi.stubGlobal('fetch', fetchMock)
    expect((await registerEmbeddingModel({ name: ' dino ', version: 'v3' })).id).toBe(3)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: 'dino', version: 'v3' })
    await registerEmbeddingModel({})
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({})
    await expect(registerEmbeddingModel({ version: 'a b' })).rejects.toThrow('只能用字母')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('starts and stops a build of the version asked for', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, build: buildWire() }))
      .mockResolvedValueOnce(json({ ok: true, build: buildWire({ state: 'stopping' }) }))
      .mockResolvedValueOnce(json({ ok: true, build: null }))
      .mockResolvedValueOnce(json({ ok: true, build: buildWire({ model_id: 1 }) }))
    vi.stubGlobal('fetch', fetchMock)
    expect((await startModelBuild(2)).state).toBe('running')
    expect((await stopModelBuild(2))?.state).toBe('stopping')
    expect(await stopModelBuild(2)).toBeNull()  // nothing was running
    await expect(startModelBuild(2)).rejects.toMatchObject({ code: 'MODEL_PROTOCOL_ERROR' })  // another version's build
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
      ['/api/embedding-models/2/build', 'POST'], ['/api/embedding-models/2/build/stop', 'POST'],
      ['/api/embedding-models/2/build/stop', 'POST'], ['/api/embedding-models/2/build', 'POST'],
    ])
    await expect(startModelBuild(0)).rejects.toThrow('编号不正确')
  })

  it('switches, saying whether the switch needs to accept images without vectors', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, model: modelWire(2, { status: 'current', serving: true, missing: 3 }), previous_id: 1, switched: true }))
      .mockResolvedValueOnce(json({ ok: false, error: 'EMBEDDING_MODEL_INCOMPLETE', detail: '3 stored images have no vector', trace_id: 't' }, 409))
    vi.stubGlobal('fetch', fetchMock)
    expect(await activateEmbeddingModel(2, { allowMissing: true })).toMatchObject({ previousId: 1, switched: true })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ allow_missing: true })
    await expect(activateEmbeddingModel(2)).rejects.toMatchObject({ code: 'EMBEDDING_MODEL_INCOMPLETE', status: 409 })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ allow_missing: false })
  })

  it('retires, and refuses an answer about another version', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, model: modelWire(1, { status: 'retired', embedded: 0 }), dropped: true, ledger_rows: 5 }))
      .mockResolvedValueOnce(json({ ok: true, model: modelWire(3, { status: 'retired', embedded: 0 }), dropped: true, ledger_rows: 5 }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await retireEmbeddingModel(1)).toMatchObject({ dropped: true, ledgerRows: 5 })
    await expect(retireEmbeddingModel(1)).rejects.toMatchObject({ code: 'MODEL_PROTOCOL_ERROR' })
  })
})
