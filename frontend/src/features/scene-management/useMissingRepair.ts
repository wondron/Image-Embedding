import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../shared/api'
import { reembedImage } from '../../entities/image'
import { resolveMissing } from '../scene-sync'

export interface RepairFailure {
  id: string
  reason: string
}

export interface RepairState {
  running: boolean
  /** Images tried so far. */
  done: number
  total: number
  /** Got their vector back. */
  fixed: number
  /** Deleted meanwhile: nothing to repair. */
  gone: number
  failures: RepairFailure[]
  /** Ended before the last image (stopped, or the service is not up). */
  stopped: boolean
}

const REASONS: Readonly<Record<string, string>> = {
  IMAGE_FILE_UNAVAILABLE: '读不到存储里的原图，需要重新上传这张图片',
  INDEX_BAD_IMAGE: '存储里的原图无法识别为图片，需要重新上传',
  NOT_READY: '检索服务未就绪（编码模型、向量库或对象存储没有启动）',
}

function reasonOf(cause: unknown): string {
  if (cause instanceof ApiError) return (cause.code && REASONS[cause.code]) || cause.message
  return cause instanceof Error ? cause.message : '重新生成失败'
}

/**
 * Re-embed images from their stored originals, one request after another
 * (POST /api/images/{id}/reembed). Each one that gets its vector back leaves the full sync's
 * missing list. Leaving the page stops after the image in flight; NOT_READY stops at once, since
 * every image after it would fail the same way.
 */
export function useMissingRepair(apiBase: string) {
  const [repair, setRepair] = useState<RepairState | null>(null)
  const stopRef = useRef(false)
  const runningRef = useRef(false)
  useEffect(() => () => { stopRef.current = true }, [])

  async function startRepair(ids: readonly string[]) {
    if (runningRef.current || ids.length === 0) return
    runningRef.current = true
    stopRef.current = false
    let state: RepairState = { running: true, done: 0, total: ids.length, fixed: 0, gone: 0, failures: [], stopped: false }
    setRepair(state)
    try {
      for (const id of ids) {
        if (stopRef.current) { state = { ...state, stopped: true }; break }
        let halt = false
        try {
          const result = await reembedImage(id, { baseUrl: apiBase })
          if (result.vector.status === 'missing') {
            state = { ...state, failures: [...state.failures, { id, reason: '写入后向量库里仍然没有它' }] }
          } else {
            state = { ...state, fixed: state.fixed + 1 }
            resolveMissing([id])
          }
        } catch (cause) {
          if (cause instanceof ApiError && cause.code === 'IMAGE_NOT_FOUND') {
            state = { ...state, gone: state.gone + 1 }
            resolveMissing([id])
          } else {
            state = { ...state, failures: [...state.failures, { id, reason: reasonOf(cause) }] }
            halt = cause instanceof ApiError && cause.code === 'NOT_READY'
          }
        }
        state = { ...state, done: state.done + 1, stopped: halt && state.done + 1 < state.total }
        setRepair(state)
        if (halt) break
      }
    } finally {
      runningRef.current = false
      setRepair({ ...state, running: false })
    }
  }

  return { repair, startRepair, stopRepair: () => { stopRef.current = true } }
}
