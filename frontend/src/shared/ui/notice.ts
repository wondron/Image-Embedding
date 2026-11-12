/**
 * The app's one way to tell the person how something went: a page sends a signal
 * (`notify.success('已保存。')`) and the single <StatusOverlay /> mounted at the root shows it.
 * Callable from anywhere, React or not; nothing else renders feedback of its own.
 *
 * Success and info are light toasts: they never block the page and close by themselves.
 * Warnings and errors are a popup that stays until the person closes it, so a reason or a trace id
 * is never gone before it was read; such a popup is never replaced by a later one (those wait).
 */

export type NoticeType = 'success' | 'info' | 'warn' | 'error'

export interface NoticeAction {
  label: string
  /** Opens in a new tab (e.g. 「打开原图」). */
  href?: string
  /** Runs, then the notice closes (e.g. 「去同步」). */
  onClick?: () => void
}

export interface NoticeOptions {
  type?: NoticeType
  /** Defaults by type: 操作成功 / 提示 / 请注意 / 操作失败 (a toast shows only a title that was given). */
  title?: string
  message: string
  /** Lines under the message, e.g. one per file that was not added. */
  details?: readonly string[]
  /** Milliseconds before it closes by itself; 0 keeps it open. Default: by type, toasts longer for longer text. */
  duration?: number
  /** Popup only: a click on the backdrop closes it (the buttons and Esc always do). Default true. */
  closable?: boolean
  action?: NoticeAction
}

export interface Notice {
  id: number
  type: NoticeType
  title: string
  message: string
  details: readonly string[]
  duration: number
  closable: boolean
  action: NoticeAction | null
}

export const NOTICE_TITLE: Readonly<Record<NoticeType, string>> = { success: '操作成功', info: '提示', warn: '请注意', error: '操作失败' }
/** Base display time; a toast gets NOTICE_MS_PER_CHAR more for each character beyond 20, up to NOTICE_MAX_TOAST_MS. */
export const NOTICE_DURATION: Readonly<Record<NoticeType, number>> = { success: 2400, info: 3600, warn: 0, error: 0 }
export const NOTICE_MS_PER_CHAR = 60
export const NOTICE_MAX_TOAST_MS = 8000
/** Toasts shown at once; the oldest goes when another comes. */
export const TOAST_LIMIT = 3
/** Popups waiting behind one that stays; older ones beyond this are dropped. */
export const NOTICE_QUEUE_LIMIT = 5

/** Success and info are toasts; warnings and errors are the popup. */
export function isToast(type: NoticeType): boolean {
  return type === 'success' || type === 'info'
}

export interface NoticeStore {
  /** Send a signal: a toast, or the popup now or after the one waiting to be read. Returns its id. */
  emit: (options: NoticeOptions) => number
  /** Close that notice (a toast, the popup, or one waiting); no id: the popup showing. */
  close: (id?: number) => void
  /** Close everything. */
  clear: () => void
  /** The popup showing (a warning or an error). */
  current: () => Notice | null
  /** Popups waiting behind it. */
  pending: () => readonly Notice[]
  /** The toasts showing, oldest first. */
  toasts: () => readonly Notice[]
  subscribe: (listener: () => void) => () => void
}

function sameContent(a: Notice, b: Notice): boolean {
  return a.type === b.type && a.title === b.title && a.message === b.message && a.details.join('\n') === b.details.join('\n')
}

function defaultDuration(type: NoticeType, message: string): number {
  if (!isToast(type)) return NOTICE_DURATION[type]
  const extra = Math.max(0, Array.from(message).length - 20) * NOTICE_MS_PER_CHAR
  return Math.min(NOTICE_MAX_TOAST_MS, NOTICE_DURATION[type] + extra)
}

export function createNoticeStore(): NoticeStore {
  let nextId = 0
  let shown: Notice | null = null
  let waiting: Notice[] = []
  let toasts: Notice[] = []
  const listeners = new Set<() => void>()
  const publish = () => listeners.forEach(listener => listener())

  function normalize(options: NoticeOptions): Notice {
    const type = options.type ?? 'info'
    const duration = options.duration ?? defaultDuration(type, options.message)
    return {
      id: ++nextId,
      type,
      title: options.title?.trim() || NOTICE_TITLE[type],
      message: options.message,
      details: options.details ?? [],
      duration: Number.isFinite(duration) && duration > 0 ? duration : 0,
      closable: options.closable ?? true,
      action: options.action ?? null,
    }
  }

  return {
    emit(options) {
      const notice = normalize(options)
      if (isToast(notice.type)) {
        // The same again moves to the end with a fresh countdown; one more than the limit drops the oldest
        toasts = [...toasts.filter(toast => !sameContent(toast, notice)), notice].slice(-TOAST_LIMIT)
      } else if (shown && sameContent(shown, notice)) {
        shown = { ...notice }
      } else if (waiting.some(entry => sameContent(entry, notice))) {
        return waiting.find(entry => sameContent(entry, notice))!.id
      } else if (!shown || shown.duration > 0) {
        // Nothing showing, or one that would close by itself anyway: the newer one replaces it
        shown = notice
      } else {
        waiting = [...waiting, notice].slice(-NOTICE_QUEUE_LIMIT)
      }
      publish()
      return notice.id
    },
    close(id) {
      if (id !== undefined && toasts.some(toast => toast.id === id)) {
        toasts = toasts.filter(toast => toast.id !== id)
        publish()
        return
      }
      if (id !== undefined && shown?.id !== id) {
        const before = waiting.length
        waiting = waiting.filter(entry => entry.id !== id)
        if (waiting.length !== before) publish()
        return
      }
      if (!shown) return
      shown = waiting[0] ?? null
      waiting = waiting.slice(1)
      publish()
    },
    clear() {
      if (!shown && !waiting.length && !toasts.length) return
      shown = null
      waiting = []
      toasts = []
      publish()
    },
    current: () => shown,
    pending: () => waiting,
    toasts: () => toasts,
    subscribe(listener) {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
  }
}

/** The app's store; <StatusOverlay /> shows what it holds. */
export const noticeStore = createNoticeStore()

type Shortcut = (message: string, options?: Omit<NoticeOptions, 'type' | 'message'>) => number

/** Send a signal: `notify({ type, title, message, … })`, or one of the shortcuts. */
export const notify = Object.assign(
  (options: NoticeOptions) => noticeStore.emit(options),
  {
    success: ((message, options) => noticeStore.emit({ ...options, type: 'success', message })) as Shortcut,
    info: ((message, options) => noticeStore.emit({ ...options, type: 'info', message })) as Shortcut,
    warn: ((message, options) => noticeStore.emit({ ...options, type: 'warn', message })) as Shortcut,
    error: ((message, options) => noticeStore.emit({ ...options, type: 'error', message })) as Shortcut,
    close: (id?: number) => noticeStore.close(id),
    clear: () => noticeStore.clear(),
  },
)
