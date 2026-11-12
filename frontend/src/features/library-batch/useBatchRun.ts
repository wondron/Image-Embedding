import { useCallback, useEffect, useRef, useState } from 'react'
import type { LibraryImageItem } from '../../entities/image'
import { runBatch, type BatchOutcome } from './runBatch'

/** Requests in flight at once: each write also pushes to the vector store. */
const CONCURRENCY = 3

export type BatchPhase = 'idle' | 'running' | 'stopping' | 'finished'

export interface BatchRun {
  phase: BatchPhase
  /** Every image of the batch, in selection order. */
  items: readonly LibraryImageItem[]
  /** By image id; an image without one was not reached (stopped). */
  outcomes: ReadonlyMap<string, BatchOutcome>
}

export type BatchWork = (item: LibraryImageItem, signal: AbortSignal) => Promise<BatchOutcome>

/**
 * A batch over library images: start, stop (the requests already sent finish), retry some of them.
 * Leaving the page aborts what is in flight.
 */
export function useBatchRun() {
  const [run, setRun] = useState<BatchRun>({ phase: 'idle', items: [], outcomes: new Map() })
  const current = useRef(run)
  current.current = run
  const stopRequested = useRef(false)
  const lifetime = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    return () => controller.abort()
  }, [])

  /** Runs `work` for `targets` (all of `items`, or the ones to retry); resolves with every outcome so far. */
  const start = useCallback(async (items: readonly LibraryImageItem[], targets: readonly LibraryImageItem[], work: BatchWork) => {
    const signal = lifetime.current?.signal
    if (!signal || signal.aborted) return new Map<string, BatchOutcome>()
    stopRequested.current = false
    const retried = new Set(targets.map(item => item.id))
    // Kept here, not read back from state: state updates land later than the last result
    const outcomes = new Map([...(current.current.items === items ? current.current.outcomes : [])].filter(([id]) => !retried.has(id)))
    setRun({ phase: 'running', items, outcomes: new Map(outcomes) })
    await runBatch(targets, item => work(item, signal), {
      concurrency: CONCURRENCY,
      stopped: () => stopRequested.current || signal.aborted,
      onResult: (item, outcome) => {
        if (signal.aborted) return
        outcomes.set(item.id, outcome)
        setRun(previous => ({ ...previous, outcomes: new Map(outcomes) }))
      },
    })
    if (!signal.aborted) setRun(previous => ({ ...previous, phase: 'finished', outcomes: new Map(outcomes) }))
    return outcomes
  }, [])

  const stop = useCallback(() => {
    stopRequested.current = true
    setRun(current => (current.phase === 'running' ? { ...current, phase: 'stopping' } : current))
  }, [])

  return { run, start, stop }
}
