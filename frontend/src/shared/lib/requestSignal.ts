/**
 * A signal for one request: it aborts when `parent` does (the page is leaving), or after
 * `timeoutMs` with a TimeoutError, so a caller can tell "no answer in time" from "cancelled".
 */
export function requestSignal(parent: AbortSignal, timeoutMs: number): AbortSignal {
  const controller = new AbortController()
  const timer = window.setTimeout(() => {
    controller.abort(new DOMException('请求超时', 'TimeoutError'))
  }, timeoutMs)
  const abortFromParent = () => controller.abort(
    parent.reason instanceof Error ? parent.reason : new DOMException('Aborted', 'AbortError'),
  )
  if (parent.aborted) abortFromParent()
  else parent.addEventListener('abort', abortFromParent, { once: true })
  controller.signal.addEventListener('abort', () => {
    window.clearTimeout(timer)
    parent.removeEventListener('abort', abortFromParent)
  })
  return controller.signal
}
