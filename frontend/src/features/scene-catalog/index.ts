import { createContext, useContext, useEffect, useSyncExternalStore } from 'react'
import { listScenes, sceneError, toApiSceneCode, type Scene, type SceneCatalog } from '../../entities/scene'
export const SceneApiContext = createContext('')
type State = { data: SceneCatalog | null; loading: boolean; error: string | null; revision: number; pendingSync: boolean }
type Entry = { state: State; generation: number; promise: Promise<void> | null; listeners: Set<() => void> }
const entries = new Map<string, Entry>()
function entry(base: string): Entry { let e = entries.get(base); if (!e) { e = { state: { data: null, loading: false, error: null, revision: 0, pendingSync: false }, generation: 0, promise: null, listeners: new Set() }; entries.set(base, e) } return e }
function emit(e: Entry, patch: Partial<State>) { e.state = { ...e.state, ...patch }; e.listeners.forEach(fn => fn()) }
export function refreshSceneCatalog(base: string, invalidate = false): Promise<void> {
  const e = entry(base)
  if (invalidate) { e.generation++; e.promise = null }
  if (e.promise) return e.promise
  const generation = e.generation
  emit(e, { loading: true, error: null })
  const promise = listScenes(base).then(data => { if (generation === e.generation) emit(e, { data, error: null, revision: e.state.revision + 1 }) }).catch(error => { if (generation === e.generation) emit(e, { error: sceneError(error) }) }).finally(() => { if (generation === e.generation) { e.promise = null; emit(e, { loading: false }) } })
  e.promise = promise; return promise
}
/**
 * Apply a configuration write to the shared catalog, then re-read it.
 * syncRequired (from the server): the change reaches stored images only through the full sync;
 * the pending flag stays on until a sync round finishes.
 */
export function sceneSaved(base: string, scene?: Scene | null, deletedId?: number | null, syncRequired = true) {
  const e = entry(base)
  const pendingSync = e.state.pendingSync || syncRequired
  if (e.state.data) {
    const scenes = e.state.data.scenes.filter(s => s.id !== deletedId).map(s => s.id === scene?.id ? scene : s)
    if (scene && !scenes.some(s => s.id === scene.id)) scenes.push(scene)
    emit(e, { data: { ...e.state.data, scenes }, pendingSync })
  } else emit(e, { pendingSync })
  return refreshSceneCatalog(base, true)
}
export function markScenePending(base: string, pendingSync = true) { emit(entry(base), { pendingSync }) }
/** The shared catalog state of one API base, outside React (useSceneCatalog subscribes to it). */
export function sceneCatalogState(base: string): Readonly<State> { return entry(base).state }
export function useSceneCatalog(explicitBase?: string) {
  const context = useContext(SceneApiContext); const base = explicitBase ?? context
  const e = entry(base)
  const state = useSyncExternalStore(fn => { e.listeners.add(fn); return () => { e.listeners.delete(fn) } }, () => e.state)
  useEffect(() => { if (!e.state.data && !e.promise && !e.state.error) void refreshSceneCatalog(base) }, [base, e])
  const active = state.data?.scenes.filter(s => s.isActive) ?? []
  const defaultScene = active.find(s => s.code === state.data?.defaultScene)
  const valid = Boolean(state.data && !state.loading && !state.error && defaultScene)
  return { ...state, base, active, defaultScene, valid, refresh: () => refreshSceneCatalog(base, true) }
}
export function useSceneSelection(value: string, onChange: (code: string) => void, explicitBase?: string) {
  const catalog = useSceneCatalog(explicitBase)
  const canonical = toApiSceneCode(value, '')
  const present = catalog.active.some(s => s.code === canonical)
  useEffect(() => {
    if (catalog.valid && (!present || canonical !== value)) onChange(present ? canonical : catalog.defaultScene!.code)
  }, [catalog.valid, catalog.revision, present, canonical, value, onChange, catalog.defaultScene])
  return { ...catalog, selectionValid: catalog.valid && present, fallback: Boolean(catalog.data && !present && catalog.defaultScene), configurationError: Boolean(catalog.data && !catalog.defaultScene) }
}
