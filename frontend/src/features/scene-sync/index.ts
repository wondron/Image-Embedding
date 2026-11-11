import { useSyncExternalStore } from 'react'
import { sceneError, syncScenes, type SyncBatch } from '../../entities/scene'
import { markScenePending, refreshSceneCatalog } from '../scene-catalog'
type SyncState = { writes: number; base: string | null; running: boolean; inFlight: boolean; after: string | null; limit: number; status: 'idle' | 'running' | 'paused' | 'failed' | 'done'; error: string | null; batch: SyncBatch | null; batches: number; processed: number; changed: number; pushed: number; missing: string[] }
let state: SyncState = { writes: 0, base: null, running: false, inFlight: false, after: null, limit: 200, status: 'idle', error: null, batch: null, batches: 0, processed: 0, changed: 0, pushed: 0, missing: [] }
let schedule = false
const listeners = new Set<() => void>()
const successful = new Set<string>()
function update(patch: Partial<SyncState>) { state = { ...state, ...patch }; listeners.forEach(fn => fn()) }
export function sceneWritesLocked() { return state.running || state.inFlight }
export function beginSceneWrite() { if (sceneWritesLocked()) return false; update({ writes: state.writes + 1 }); return true }
export function endSceneWrite() { update({ writes: Math.max(0, state.writes - 1) }) }
export function useSceneSync() { return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn) } }, () => state) }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
async function run() {
  const base = state.base!; const after = state.after; const limit = state.limit
  update({ running: true, inFlight: true, status: 'running', error: null })
  try {
    const batch = await syncScenes(base, after, limit)
    // Count accepted success exactly once per requested cursor. Keep missing even on protocol failure.
    const key = `${after ?? '<start>'}:${limit}`; const counted = successful.has(key)
    successful.add(key)
    update({ batch, batches: state.batches + (counted ? 0 : 1), processed: state.processed + (counted ? 0 : batch.processed), changed: state.changed + (counted ? 0 : batch.membershipChanged), pushed: state.pushed + (counted ? 0 : batch.payloadPushed), missing: [...new Set([...state.missing, ...batch.payloadMissing])] })
    if (!batch.done && (!batch.nextAfter || !uuid.test(batch.nextAfter) || (after !== null && batch.nextAfter.toLowerCase() <= after.toLowerCase()))) throw new Error('同步协议异常：next_after 缺失或未前移，已停止后续批；保留原游标。')
    update({ after: batch.nextAfter, inFlight: false })
    if (batch.done) {
      schedule = false; update({ running: false, status: 'done' }); markScenePending(base, false); await refreshSceneCatalog(base, true)
    } else if (schedule) { await run() }
    else { update({ running: false, status: 'paused' }); await refreshSceneCatalog(base, true) }
  } catch (error) {
    schedule = false; update({ running: false, inFlight: false, status: 'failed', error: sceneError(error) })
    if (state.batches) await refreshSceneCatalog(base, true)
  }
}
export function startSceneSync(base: string, limit: number, restart = true) {
  if (sceneWritesLocked() || state.writes > 0 || !Number.isInteger(limit) || limit < 1 || limit > 1000) return
  if (!restart && (state.base !== base || state.status === 'idle' || state.status === 'done')) return
  if (restart) { successful.clear(); update({ base, after: null, limit, batch: null, batches: 0, processed: 0, changed: 0, pushed: 0, missing: state.base === base ? state.missing : [] }) }
  schedule = true; void run()
}
/** Images that got their vector back (re-embedded) or are gone: no longer missing. */
export function resolveMissing(ids: readonly string[]) {
  if (!ids.length) return
  const done = new Set(ids.map(id => id.toLowerCase()))
  update({ missing: state.missing.filter(id => !done.has(id.toLowerCase())) })
}
export function stopSceneSync() {
  schedule = false
  if (state.running && !state.inFlight) update({ running: false, status: 'paused' })
  if (!state.inFlight && state.batches && state.base) void refreshSceneCatalog(state.base, true)
}
