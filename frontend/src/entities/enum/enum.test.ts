import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../shared/api'
import { addEnumValue, getEnum, listEnums } from './api'
import { EMPTY_ENUM_CATALOG, enumOptions, enumValueLabel, enumValueText, toEnumCatalog } from './catalog'
import { toEnumFailure } from './errors'
import { toEnumDetail, toEnumList } from './model'
import { decidePrePost, resolveUncertainProbe, validateAppendDraft } from './policy'

const labels = { service: '枚举服务', endpoint: '枚举接口', action: '枚举配置' }
const json = (body: unknown, status = 200, trace = 'trace-h') => new Response(JSON.stringify(body), {
  status,
  headers: { 'X-Trace-Id': trace },
})
const item = {
  name: 'food_state',
  label: '食物状态',
  description: '状态',
  editable: true,
  values: [{ value: 'raw', label: '生', description: '' }],
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('enum parsers', () => {
  it('reads a healthy list from the header trace and keeps file_error only when it has text', () => {
    expect(toEnumList({ ok: true, enums: [item] }, 'trace-h')).toEqual({
      enums: [item],
      fileError: null,
      traceId: 'trace-h',
    })
    expect(toEnumList({ ok: true, enums: [item], file_error: '  invalid YAML  ' }, 'trace-h')?.fileError).toBe('invalid YAML')
    expect(toEnumList({ ok: true, enums: [], file_error: '   ' }, null)?.fileError).toBeNull()
    expect(toEnumList({ ok: true, enums: [], file_error: { reason: 'no' } }, null)?.fileError).toBeNull()
  })

  it('treats a missing editable flag as not editable and does not read file_error from a detail body', () => {
    const detail = toEnumDetail({
      ok: true,
      enum: { ...item, editable: undefined, file_error: 'should-not-stick' },
      file_error: 'should-not-stick',
    }, 'trace-d')
    expect(detail).toEqual({
      item: { ...item, editable: false },
      traceId: 'trace-d',
    })
    expect(detail && 'fileError' in detail).toBe(false)
  })
})

describe('enum catalog (labels for stored values)', () => {
  const catalog = toEnumCatalog([{
    ...item,
    values: [
      { value: 'raw', label: '生', description: '' },
      { value: 'thawed', label: '解冻', description: '' },
      { value: 'unnamed', label: '  ', description: '' },
    ],
  }])

  it('shows the server label, else the value as stored, and 「—」 when there is no value', () => {
    expect(enumValueText(catalog, 'food_state', 'thawed')).toBe('解冻')
    expect(enumValueText(catalog, 'food_state', 'half_baked')).toBe('half_baked')
    expect(enumValueText(catalog, 'food_state', 'unnamed')).toBe('unnamed')
    expect(enumValueText(catalog, 'license', 'raw')).toBe('raw')
    expect(enumValueText(EMPTY_ENUM_CATALOG, 'food_state', 'raw')).toBe('raw')
    for (const value of [null, undefined, '']) expect(enumValueText(catalog, 'food_state', value)).toBe('—')
    expect(enumValueLabel(catalog, 'food_state', 'raw')).toBe('生')
    expect(enumValueLabel(catalog, 'food_state', 'half_baked')).toBeNull()
  })

  it('lists options in file order and keeps a current value the catalog does not list', () => {
    expect(enumOptions(catalog, 'food_state')).toEqual([
      { value: 'raw', label: '生' },
      { value: 'thawed', label: '解冻' },
      { value: 'unnamed', label: 'unnamed' },
    ])
    expect(enumOptions(catalog, 'food_state', 'raw')).toHaveLength(3)
    expect(enumOptions(catalog, 'food_state', 'half_baked').at(-1)).toEqual({ value: 'half_baked', label: 'half_baked' })
    expect(enumOptions(EMPTY_ENUM_CATALOG, 'food_state', null)).toEqual([])
  })
})

describe('validateAppendDraft', () => {
  it('accepts boundary values and trims only value and label', () => {
    expect(validateAppendDraft({ value: 'a', label: '名', description: '' })).toEqual({
      ok: true, body: { value: 'a', label: '名', description: '' },
    })
    expect(validateAppendDraft({ value: 'a'.repeat(32), label: '名'.repeat(64), description: '说'.repeat(256) })).toMatchObject({ ok: true })
    expect(validateAppendDraft({ value: ' a_b ', label: ' 名称 ', description: '  保留空格  ' })).toEqual({
      ok: true, body: { value: 'a_b', label: '名称', description: '  保留空格  ' },
    })
  })

  it('rejects invalid value, label and description without a request payload', () => {
    for (const value of ['', '   ', 'a'.repeat(33), '1abc', 'Abc', 'a-b', '中文']) {
      const result = validateAppendDraft({ value, label: '名称', description: '' })
      expect(result.ok).toBe(false)
      if (!result.ok) expect(result.fieldErrors.value).toBeTruthy()
    }
    expect(validateAppendDraft({ value: 'ab', label: ' ', description: '' }).ok).toBe(false)
    expect(validateAppendDraft({ value: 'ab', label: '名'.repeat(65), description: '' }).ok).toBe(false)
    const longDescription = validateAppendDraft({ value: 'ab', label: '名称', description: '说'.repeat(257) })
    expect(longDescription.ok).toBe(false)
    if (!longDescription.ok) expect(longDescription.fieldErrors.description).toBeTruthy()
  })
})

describe('write gates', () => {
  const editable = { editable: true, values: Array.from({ length: 99 }, () => ({})) }

  it('allows 99 values and stops at 100, file_error, readonly, missing and stale results', () => {
    expect(decidePrePost({ fresh: true, fileError: null, item: editable })).toEqual({ action: 'post' })
    expect(decidePrePost({ fresh: true, fileError: null, item: { editable: true, values: Array.from({ length: 100 }, () => ({})) } })).toEqual({ action: 'stop', reason: 'full' })
    expect(decidePrePost({ fresh: true, fileError: 'broken', item: editable })).toEqual({ action: 'stop', reason: 'file_error' })
    expect(decidePrePost({ fresh: true, fileError: null, item: { editable: false, values: [] } })).toEqual({ action: 'stop', reason: 'not_editable' })
    expect(decidePrePost({ fresh: true, fileError: null, item: null })).toEqual({ action: 'stop', reason: 'missing' })
    expect(decidePrePost({ fresh: false, fileError: null, item: editable })).toEqual({ action: 'stop', reason: 'stale' })
  })

  it('keeps an uncertain value blocked until a fresh healthy read proves it is absent', () => {
    const present = { editable: true, values: [{ value: 'thawed' }] }
    const absent = { editable: true, values: [{ value: 'raw' }] }
    expect(resolveUncertainProbe({ probeOk: false, fileError: null, item: absent, value: 'thawed' })).toEqual({ action: 'keep_blocked', reason: 'probe_failed' })
    expect(resolveUncertainProbe({ probeOk: true, fileError: 'broken', item: present, value: 'thawed' })).toEqual({ action: 'exists' })
    expect(resolveUncertainProbe({ probeOk: true, fileError: 'broken', item: absent, value: 'thawed' })).toEqual({ action: 'keep_blocked', reason: 'file_error' })
    expect(resolveUncertainProbe({ probeOk: true, fileError: null, item: { editable: false, values: [] }, value: 'thawed' })).toEqual({ action: 'keep_blocked', reason: 'not_editable' })
    expect(resolveUncertainProbe({ probeOk: true, fileError: null, item: absent, value: 'thawed' })).toEqual({ action: 'allow_retry' })
  })
})

describe('toEnumFailure', () => {
  it('maps the enum error codes and leaves unknown codes retryable only by the caller', () => {
    expect(toEnumFailure(new ApiError('no', { status: 403, code: 'ENUM_NOT_EDITABLE', detail: 'readonly', traceId: 't' }))).toMatchObject({
      kind: 'not_editable', blockWrites: false, detail: 'readonly',
    })
    expect(toEnumFailure(new ApiError('dup', { status: 409, code: 'ENUM_VALUE_EXISTS', detail: 'exists', traceId: 't' })).fieldErrors).toEqual({ value: '该取值已存在。' })
    expect(toEnumFailure(new ApiError('bad', {
      status: 422, code: 'VALIDATION_ERROR', detail: 'value: invalid', traceId: 't',
      errors: [{ loc: ['body', 'value'], msg: 'invalid' }],
    })).fieldErrors.value).toBe('invalid')
    expect(toEnumFailure(new ApiError('file', { status: 503, code: 'ENUM_FILE_INVALID', detail: 'broken', traceId: 't' }))).toMatchObject({
      kind: 'file_invalid', blockWrites: true,
    })
    expect(toEnumFailure(new ApiError('disk', { status: 500, code: 'ENUM_FILE_WRITE_FAILED', detail: 'disk', traceId: 't' })).kind).toBe('write_failed')
    expect(toEnumFailure(new ApiError('other', { status: 500, code: 'INTERNAL_ERROR', detail: 'boom', traceId: 't' })).kind).toBe('unknown')
    expect(toEnumFailure(new ApiError('offline', { status: null })).kind).toBe('uncertain')
    expect(toEnumFailure(new DOMException('请求超时', 'TimeoutError')).kind).toBe('uncertain')
    expect(toEnumFailure(new ApiError('gateway', { status: 504, code: null, detail: 'timeout' })).kind).toBe('uncertain')
  })
})

describe('enum api', () => {
  it('loads the list and one enum with the trace header', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, enums: [item] }))
      .mockResolvedValueOnce(json({ ok: true, enum: item }, 200, 'trace-one'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listEnums({ baseUrl: 'http://127.0.0.1:8320/' })).resolves.toMatchObject({ fileError: null, traceId: 'trace-h' })
    await expect(getEnum('food_state')).resolves.toMatchObject({ item: { name: 'food_state' }, traceId: 'trace-one' })
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/enums/food_state', { method: 'GET', signal: undefined })
  })

  it('posts only value, label and description, and treats an unparsed 201 as a write', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ok: true, enum: item }, 201, 'trace-w'))
      .mockResolvedValueOnce(new Response('not-json', { status: 201, headers: { 'X-Trace-Id': 'trace-u' } }))
    vi.stubGlobal('fetch', fetchMock)
    const created = await addEnumValue('food_state', { value: 'thawed', label: '解冻', description: '' })
    expect(created).toMatchObject({ outcome: 'created', status: 201, traceId: 'trace-w' })
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ value: 'thawed', label: '解冻', description: '' })
    expect(Object.keys(JSON.parse(init.body)).sort()).toEqual(['description', 'label', 'value'])
    await expect(addEnumValue('food_state', { value: 'thawed', label: '解冻', description: '' })).resolves.toEqual({
      outcome: 'created_unparsed', traceId: 'trace-u', status: 201,
    })
  })

  it('does not turn a 409 into a successful write', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      ok: false, error: 'ENUM_VALUE_EXISTS', detail: 'exists', trace_id: 'trace-e',
    }, 409)))
    await expect(addEnumValue('food_state', { value: 'raw', label: '生', description: '' })).rejects.toMatchObject({
      code: 'ENUM_VALUE_EXISTS', status: 409,
    })
    expect(labels.action).toBe('枚举配置')
  })
})
