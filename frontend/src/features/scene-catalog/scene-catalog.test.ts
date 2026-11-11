import { afterEach, describe, expect, it, vi } from 'vitest'
import { markScenePending, sceneCatalogState, sceneSaved } from '.'

const scene = (id: number, code: string) => ({
  id, code, name: code, description: null, include_all: code === 'general', outputs_heating: false, is_active: true,
  created_at: null, member_count: 0, labels: [], rules: [],
})
const catalog = () => new Response(JSON.stringify({ ok: true, scenes: [scene(1, 'general'), scene(7, 'steam_oven')], default_scene: 'general' }))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('sceneSaved', () => {
  it('marks a pending sync only for writes that need one, and keeps it until a sync round clears it', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(catalog()))
    vi.stubGlobal('fetch', fetchMock)
    const base = 'http://scene-saved.test'
    await sceneSaved(base, null, null, false) // a label edit: shows at once
    expect(sceneCatalogState(base).pendingSync).toBe(false)
    await sceneSaved(base, null, null, true) // a rule change
    expect(sceneCatalogState(base).pendingSync).toBe(true)
    await sceneSaved(base, null, null, false) // a later label edit does not clear it
    expect(sceneCatalogState(base).pendingSync).toBe(true)
    markScenePending(base, false) // what a finished sync round does
    expect(sceneCatalogState(base).pendingSync).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(3) // every write re-reads the catalog
    expect(sceneCatalogState(base).data?.scenes.map(s => s.code)).toEqual(['general', 'steam_oven'])
  })

  it('defaults to needing the sync when the caller does not know', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(catalog())))
    const base = 'http://scene-saved-default.test'
    await sceneSaved(base)
    expect(sceneCatalogState(base).pendingSync).toBe(true)
  })
})
