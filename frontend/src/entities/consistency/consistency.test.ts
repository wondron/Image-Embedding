import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  checkActive,
  getConsistencyCheck,
  startConsistencyCheck,
  stopConsistencyCheck,
  toConsistencyResponse,
  DEFAULT_CHECK_OPTIONS,
} from '.'
import { issueText, progressText, durationText, refusalText } from '../../features/consistency-check/messages'
import { ApiError } from '../../shared/api'
// A response the backend really sent (tests/test_consistency.py's scenario, through the real routes)
import fixture from './__fixtures__/report.json'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const copy = () => JSON.parse(JSON.stringify(fixture)) as typeof fixture

afterEach(() => vi.restoreAllMocks())

describe('consistency report', () => {
  it('reads a report the backend sent', () => {
    const run = toConsistencyResponse(fixture)
    expect(run).toBeTruthy()
    const report = run!.report!
    expect(run!.state).toBe('done')
    expect(checkActive(run!)).toBe(false)
    expect(report.status).toBe('error')
    expect(report.connections.qdrant.url).toBe(':memory:')
    expect(report.connections.serving).toEqual({ modelId: 1, label: 'dino:v1', detail: null })
    expect(report.versions.map(v => [v.id, v.status])).toEqual([[1, 'current'], [2, 'ready'], [3, 'building']])
    const current = report.versions[0]
    expect(current.vsCurrent).toBeNull()
    expect(current.findings.lost.count).toBe(1)
    expect(current.findings.orphan_points.samples).toContain('42')
    expect(report.versions[1].vsCurrent?.onlyCurrent.count).toBe(3)
    expect(report.files?.findings.missing_original.count).toBe(1)
    expect(report.strayCollections?.map(s => s.name).sort()).toEqual(['c_retired', 'other_app'])
    expect(report.issues[0].level).toBe('error')
  })

  it('has words for every issue the backend reports', () => {
    const report = toConsistencyResponse(fixture)!.report!
    for (const issue of report.issues) {
      const text = issueText(issue, report.versions.find(v => v.id === issue.versionId))
      expect(text.title, issue.code).not.toBe(issue.code)
      expect(text.text.length, issue.code).toBeGreaterThan(0)
    }
    // the codes build_issues can give (services/retrieval/consistency.py)
    const codes = ['postgres_unreachable', 'qdrant_unreachable', 'storage_unreachable', 'qdrant_restart_required', 'no_current_model',
      'retrieval_not_ready', 'serving_not_current', 'deletion_queue', 'version_unreadable', 'collection_missing', 'collection_mismatch',
      'missing_vectors', 'lost_vectors', 'unrecorded_vectors', 'stale_payloads', 'ledger_behind', 'orphan_points', 'pending_points',
      'versions_differ', 'encoder_mismatch', 'weights_unavailable', 'retired_collection_left', 'stray_collections', 'collections_unlisted',
      'files_unchecked', 'missing_original', 'missing_thumb', 'files_unreadable', 'content_mismatch', 'orphan_files', 'pending_files',
      'file_list_failed', 'file_list_truncated', 'check_incomplete']
    for (const code of codes) {
      expect(issueText({ level: 'warn', code, count: 2, versionId: null, label: 'dino:v1', detail: 'x' }).title, code).not.toBe(code)
    }
  })

  it('points each issue at the page that fixes it, by the version it is about', () => {
    const issue = { level: 'warn' as const, code: 'missing_vectors', count: 3, versionId: 1, label: 'dino:v1', detail: null }
    const report = toConsistencyResponse(fixture)!.report!
    expect(issueText(issue, report.versions[0]).fix?.view).toBe('models')
    expect(issueText(issue, report.versions[0]).text).toContain('后台对账')
    expect(issueText(issue, report.versions[1]).text).toContain('切换到这个版本之前')
    const stale = { ...issue, code: 'stale_payloads' }
    expect(issueText(stale, report.versions[0]).fix).toEqual({ view: 'scenes', label: '去「场景管理」全量同步', params: { sceneTab: 'sync' } })
    expect(issueText(stale, report.versions[1]).fix).toBeUndefined()  // 全量同步只写当前版本
  })

  it('an answer that does not read cleanly is unreadable as a whole', () => {
    expect(toConsistencyResponse({ ok: true, check: null })).toBeNull()
    expect(toConsistencyResponse({ ok: false })).toBeUndefined()
    const broken = copy()
    ;(broken.check.report.versions[0] as Record<string, unknown>).findings = { missing: { count: 1, samples: [] } }
    expect(toConsistencyResponse(broken)).toBeUndefined()
    const badState = copy()
    ;(badState.check as Record<string, unknown>).state = 'paused'
    expect(toConsistencyResponse(badState)).toBeUndefined()
    const running = copy()
    ;(running.check as Record<string, unknown>).state = 'running'
    ;(running.check as Record<string, unknown>).report = null
    const run = toConsistencyResponse(running)
    expect(run?.report).toBeNull()
    expect(checkActive(run!)).toBe(true)
  })

  it('calls the three endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ ok: true, check: null }))
      .mockResolvedValueOnce(json(fixture))
      .mockResolvedValueOnce(json(fixture))
    expect(await getConsistencyCheck({ baseUrl: 'http://api' })).toBeNull()
    const run = await startConsistencyCheck({ ...DEFAULT_CHECK_OPTIONS, files: false, content: true }, { baseUrl: 'http://api' })
    expect(run.state).toBe('done')
    await stopConsistencyCheck({ baseUrl: 'http://api' })
    expect(fetchMock.mock.calls.map(call => [String(call[0]), (call[1] as RequestInit).method ?? 'GET'])).toEqual([
      ['http://api/api/consistency', 'GET'], ['http://api/api/consistency/check', 'POST'], ['http://api/api/consistency/check/stop', 'POST'],
    ])
    // content needs files: never asked for without them
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual(
      { vectors: true, files: false, orphans: true, content: false, encoders: true },
    )
  })

  it('a refusal says why', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ ok: false, error: 'CONSISTENCY_CHECK_RUNNING', detail: 'a consistency check is running', trace_id: 't1' }, 409))
    const cause = await startConsistencyCheck(DEFAULT_CHECK_OPTIONS).catch(e => e)
    expect(cause).toBeInstanceOf(ApiError)
    expect(refusalText(cause)).toContain('已经在核对了')
    expect(refusalText(cause)).toContain('t1')
  })

  it('progress and durations in words', () => {
    expect(progressText('images', 1500, 15231, null)).toBe('逐张核对图片（向量和文件） · 1500 / 15231')
    expect(progressText('points', 0, null, 'dino:v1')).toBe('翻查向量库里多余的点（dino:v1）')
    expect(durationText(40)).toBe('不到 1 秒')
    expect(durationText(4200)).toBe('4.2 秒')
    expect(durationText(125_000)).toBe('2 分 05 秒')
  })
})
