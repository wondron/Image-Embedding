import { afterEach, expect, it, vi } from 'vitest'
import { importCategoryTree, importSceneConfig } from './api'
import { importChangeCount, toImportReport } from './model'

afterEach(() => vi.unstubAllGlobals())
const report = { ok: true, dry_run: true, summary: { total: 2, create: 1, unchanged: 1 }, rows: [] }

it('keeps preview/apply and prune explicit, posting the document as JSON', async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(report))))
  vi.stubGlobal('fetch', fetch)
  const document = { kind: 'category_tree', categories: [] }
  await importCategoryTree(document, { dryRun: true })
  expect(fetch.mock.calls[0][0]).toBe('/api/categories/import?dry_run=true')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(document)
  await importSceneConfig({ kind: 'scene_config', scenes: [] }, { dryRun: false, prune: true })
  expect(fetch.mock.calls[1][0]).toBe('/api/scenes/import?dry_run=false&prune=true')
})

it('retains partial errors, truncation and sync requirements without treating unchanged as writes', () => {
  const mapped = toImportReport({ ...report, prune: true, sync_required: true, rows_truncated: true,
    summary: { total: 4, create: 1, update: 1, unchanged: 1, error: 1, not_in_file: 2 },
    rows: [{ kind: 'rule', key: 'oven/category:dish.pizza', action: 'error', error: 'MISSING', detail: 'category missing' }],
  }, 'import-t')!
  expect(importChangeCount(mapped.summary)).toBe(2)
  expect(mapped).toMatchObject({ traceId: 'import-t', prune: true, syncRequired: true, rowsTruncated: true,
    rows: [{ kind: 'rule', action: 'error', error: 'MISSING' }] })
  expect(toImportReport({ ...report, dry_run: 'true' }, null)).toBeNull()
})

it('rejects an invalid success envelope instead of presenting a successful import', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }))))
  await expect(importCategoryTree({}, { dryRun: false })).rejects.toThrow('响应不符合约定')
})
