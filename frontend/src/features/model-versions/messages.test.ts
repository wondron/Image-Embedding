import { describe, expect, it } from 'vitest'
import { toModelOverview } from '../../entities/embedding-model'
import { ApiError } from '../../shared/api'
import { actionsFor, defaultVersion, guideFor, modelFileStem, refusalText } from './messages'

const model = (id: number, fields: Record<string, unknown> = {}) => ({
  id, name: 'dino', version: `v${id}`, status: 'ready', collection: `dino_v${id}`, dim: 4, distance: 'cosine',
  weights_uri: `w${id}.onnx`, created_at: null, activated_at: null, embedded: 5, missing: 0, behind: 0,
  points: 5, weights: 'ok', artifact: null,
  fingerprint: true, configured: false, serving: false, encoder: 'recorded', ...fields,
})
const current = model(1, { status: 'current', serving: true, encoder: 'serving' })
const overview = (models: unknown[], extra: Record<string, unknown> = {}) => toModelOverview({
  ok: true, images: 5, build: null, models, switch_catch_up_max: 200,
  configured: { loaded: true, model_path: 'config/model/dino/new.onnx', dim: 4, artifact: null, model_id: null, detail: null },
  retrieval: { ready: true, model_id: 1, encoder: 'own', code: null, detail: null },
  ...extra,
})!
const configured = (id: number | null) => ({ configured: { loaded: true, model_path: 'config/model/dino/new.onnx', dim: 4, artifact: null, model_id: id, detail: null } })
const buildWire = { model_id: 2, state: 'running', started_at: 't', finished_at: null, verified: 0, repaired: 0, embedded: 2, failed: 0, gone: 0, payloads: 0, passes: 1, remaining: null, error: null, failures: [] }

describe('guideFor: the next step of a model update', () => {
  it('starts with registering a configured model nobody registered', () => {
    const guide = guideFor(overview([current]))
    expect(guide).toMatchObject({ step: 'register', action: { kind: 'register' }, tone: 'info' })
    expect(guide.text).toContain('config/model/dino/new.onnx')
    expect(guide.text).toContain('检索继续用 dino:v1')
  })

  it('then building its vectors, then switching, then done', () => {
    const building = model(2, { status: 'building', embedded: 2, missing: 3, points: 2, configured: true, encoder: 'configured' })
    expect(guideFor(overview([current, building], configured(2)))).toMatchObject({ step: 'build', action: { kind: 'build' } })
    const ready = { ...building, status: 'ready', embedded: 5, missing: 0, points: 5 }
    expect(guideFor(overview([current, ready], configured(2)))).toMatchObject({ step: 'switch', action: { kind: 'activate' } })
    const switched = [{ ...current, status: 'ready', serving: false }, { ...ready, status: 'current', serving: true }]
    expect(guideFor(overview(switched, { ...configured(2), retrieval: { ready: true, model_id: 2, encoder: 'configured', code: null, detail: null } })))
      .toMatchObject({ step: 'done', tone: 'ok', action: null })
  })

  it('offers no second build while one runs', () => {
    const building = model(2, { status: 'building', embedded: 2, missing: 3, points: 2, configured: true, encoder: 'configured' })
    expect(guideFor(overview([current, building], { ...configured(2), build: buildWire })).action).toBeNull()
  })

  it('says loudly when search stopped because the encoder and the vectors are different models', () => {
    const guide = guideFor(overview([{ ...current, serving: false, encoder: 'recorded' }], {
      retrieval: { ready: false, model_id: null, encoder: null, code: 'EMBEDDING_MODEL_MISMATCH', detail: 'weights gone' },
    }))
    expect(guide.tone).toBe('error')
    expect(guide.text).toContain('不能混用')
  })

  it('points at the gap when the current version lacks vectors', () => {
    const gap = { ...current, configured: true, encoder: 'configured', embedded: 4, missing: 1, points: 4 }
    expect(guideFor(overview([gap], { ...configured(1), retrieval: { ready: true, model_id: 1, encoder: 'configured', code: null, detail: null } })))
      .toMatchObject({ tone: 'warn', action: { kind: 'build' } })
  })

  it('cannot help while the configured model is not loaded', () => {
    expect(guideFor(overview([current], { configured: { loaded: false, model_path: null, dim: null, artifact: null, model_id: null, detail: 'no onnx' } })))
      .toMatchObject({ tone: 'warn', action: null, step: null })
  })
})

describe('actionsFor: what one version can do now', () => {
  it('builds with any encoder it can have, switches unless retired, retires only what is not current', () => {
    const data = overview([current, model(2, { status: 'building', missing: 3, embedded: 2, points: 2, encoder: 'configured' }), model(3, { activated_at: 'x' }),
      model(4, { activated_at: 'x', missing: 2, embedded: 3, points: 3 })])
    expect(actionsFor(data.models[0], data)).toEqual({ build: null, stop: false, activate: null, retire: false })
    expect(actionsFor(data.models[1], data)).toEqual({ build: '继续生成向量', stop: false, activate: '切换为当前', retire: true })
    expect(actionsFor(data.models[2], data)).toEqual({ build: null, stop: false, activate: '切回这个版本', retire: true })
    // an older version, its encoder not loaded: topped up from its own weights before a roll back
    expect(actionsFor(data.models[3], data)).toEqual({ build: '继续生成向量', stop: false, activate: '切回这个版本', retire: true })
  })

  it('a version whose weights left the image can only be retired', () => {
    const data = overview([current, model(2, { weights: 'missing', missing: 2, embedded: 3, points: 3 }), model(3, { weights: 'changed' })])
    expect(actionsFor(data.models[1], data)).toEqual({ build: null, stop: false, activate: null, retire: true })
    expect(actionsFor(data.models[2], data)).toEqual({ build: null, stop: false, activate: null, retire: true })
  })

  it('lost points are checked and embedded again', () => {
    const lost = { ...current, configured: true, encoder: 'configured', points: 3 }
    const data = overview([lost], { ...configured(1), retrieval: { ready: true, model_id: 1, encoder: 'configured', code: null, detail: null } })
    expect(actionsFor(data.models[0], data).build).toBe('核对并补齐')
    expect(guideFor(data)).toMatchObject({ tone: 'warn', title: '当前版本有向量丢了', action: { kind: 'build' } })
  })

  it('stops the version being built and locks its other actions', () => {
    const data = overview([current, model(2, { status: 'building', missing: 3, embedded: 2, points: 2, encoder: 'configured' })], { build: buildWire })
    expect(actionsFor(data.models[1], data)).toEqual({ build: null, stop: true, activate: null, retire: false })
  })

  it('a version without a known encoder cannot be switched to', () => {
    const data = overview([current, model(2, { fingerprint: false, encoder: null, weights: null })])
    expect(actionsFor(data.models[1], data).activate).toBeNull()
  })
})

describe('words', () => {
  it('names a new version like the server does', () => {
    expect(modelFileStem('config/model/dino/multitask_cls.onnx')).toBe('multitask_cls')
    expect(modelFileStem('C:\\models\\dino v3.onnx')).toBe('dino_v3')
    expect(modelFileStem(null)).toBe('model')
    expect(defaultVersion(new Date(2026, 10, 12, 15, 30))).toBe('20261112-1530')
  })

  it('explains a refusal with the server reason and the trace id', () => {
    const cause = new ApiError('切换模型版本失败', { code: 'EMBEDDING_ENCODER_UNAVAILABLE', detail: 'weights gone', traceId: 't-1', status: 409 })
    expect(refusalText(cause)).toBe('这个版本的编码器没有加载，也没能按记录重新加载它的权重。把 EMBEDDING_CONFIG 指向它的权重后再试。（weights gone） 追踪号：t-1')
    expect(refusalText(new ApiError('其他失败'))).toBe('其他失败')
  })
})
