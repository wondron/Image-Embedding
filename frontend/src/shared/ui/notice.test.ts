import { describe, expect, it, vi } from 'vitest'
import { NOTICE_DURATION, NOTICE_MAX_TOAST_MS, NOTICE_MS_PER_CHAR, NOTICE_QUEUE_LIMIT, TOAST_LIMIT, createNoticeStore, isToast } from './notice'

describe('notice store', () => {
  it('sends success and info to the toasts, warnings and errors to the popup', () => {
    expect(['success', 'info', 'warn', 'error'].map(type => isToast(type as never))).toEqual([true, true, false, false])
    const store = createNoticeStore()
    store.emit({ type: 'success', message: '已保存。' })
    store.emit({ type: 'info', message: '已取消。' })
    expect(store.current()).toBeNull()
    expect(store.toasts().map(toast => [toast.type, toast.title, toast.duration])).toEqual([
      ['success', '操作成功', NOTICE_DURATION.success], ['info', '提示', NOTICE_DURATION.info],
    ])
    store.emit({ type: 'error', message: '保存失败', title: '  ' })
    expect(store.current()).toMatchObject({ type: 'error', title: '操作失败', duration: 0, closable: true, details: [], action: null })
    expect(store.toasts()).toHaveLength(2)  // a popup does not touch the toasts
  })

  it('gives a toast more time for a longer text, up to a limit', () => {
    const store = createNoticeStore()
    store.emit({ type: 'info', message: '字'.repeat(30) })
    expect(store.toasts()[0].duration).toBe(NOTICE_DURATION.info + 10 * NOTICE_MS_PER_CHAR)
    store.emit({ type: 'success', message: '字'.repeat(500) })
    expect(store.toasts()[1].duration).toBe(NOTICE_MAX_TOAST_MS)
    store.emit({ type: 'success', message: '短', duration: 0 })
    expect(store.toasts()[2].duration).toBe(0)  // asked to stay
  })

  it('keeps a few toasts; the same text again moves to the end with a fresh countdown', () => {
    const store = createNoticeStore()
    const first = store.emit({ type: 'success', message: 'A' })
    for (const message of ['B', 'C']) store.emit({ type: 'success', message })
    const again = store.emit({ type: 'success', message: 'A' })
    expect(again).not.toBe(first)
    expect(store.toasts().map(toast => toast.message)).toEqual(['B', 'C', 'A'])
    store.emit({ type: 'info', message: 'D' })
    expect(store.toasts()).toHaveLength(TOAST_LIMIT)
    expect(store.toasts().map(toast => toast.message)).toEqual(['C', 'A', 'D'])
  })

  it('never replaces a popup that stays: later ones wait behind it, in order', () => {
    const store = createNoticeStore()
    store.emit({ type: 'error', message: '失败' })
    store.emit({ type: 'warn', message: '注意' })
    store.emit({ type: 'error', message: '又失败' })
    expect(store.current()?.message).toBe('失败')
    expect(store.pending().map(notice => notice.message)).toEqual(['注意', '又失败'])
    store.close()
    expect(store.current()?.message).toBe('注意')
    store.close()
    store.close()
    expect(store.current()).toBeNull()
  })

  it('lets a newer popup replace one that was asked to close by itself', () => {
    const store = createNoticeStore()
    store.emit({ type: 'warn', message: '稍后自动关', duration: 1500 })
    store.emit({ type: 'error', message: '失败' })
    expect(store.current()?.message).toBe('失败')
    expect(store.pending()).toEqual([])
  })

  it('shows the same popup once', () => {
    const store = createNoticeStore()
    store.emit({ type: 'error', message: '失败' })
    store.emit({ type: 'error', message: '失败' })
    store.emit({ type: 'warn', message: '注意' })
    const waiting = store.pending()[0].id
    expect(store.emit({ type: 'warn', message: '注意' })).toBe(waiting)
    expect(store.pending()).toHaveLength(1)
  })

  it('closes by id: a toast, a waiting popup, and never a newer one by a stale id', () => {
    const store = createNoticeStore()
    const toast = store.emit({ type: 'success', message: 'T' })
    const error = store.emit({ type: 'error', message: 'E' })
    const queued = store.emit({ type: 'warn', message: 'Q' })
    store.close(toast)
    expect(store.toasts()).toEqual([])
    store.close(queued)
    expect(store.pending()).toEqual([])
    store.close(error)
    expect(store.current()).toBeNull()
    const newer = store.emit({ type: 'error', message: 'N' })
    store.close(error)  // the old id again
    expect(store.current()?.id).toBe(newer)
  })

  it('keeps only the newest waiting popups', () => {
    const store = createNoticeStore()
    store.emit({ type: 'error', message: '失败' })
    for (let index = 0; index < NOTICE_QUEUE_LIMIT + 3; index += 1) store.emit({ type: 'warn', message: `第 ${index} 条` })
    expect(store.pending()).toHaveLength(NOTICE_QUEUE_LIMIT)
    expect(store.pending()[0].message).toBe('第 3 条')
  })

  it('tells its subscribers about every change, and stops when they leave', () => {
    const store = createNoticeStore()
    const listener = vi.fn()
    const leave = store.subscribe(listener)
    store.emit({ type: 'error', message: 'A' })
    store.close()
    store.close()  // nothing showing: no change, no call
    expect(listener).toHaveBeenCalledTimes(2)
    store.emit({ type: 'error', message: 'B' })
    store.emit({ type: 'success', message: 'C' })
    store.clear()
    expect([store.current(), store.pending(), store.toasts()]).toEqual([null, [], []])
    leave()
    store.emit({ type: 'info', message: 'D' })
    expect(listener).toHaveBeenCalledTimes(5)
  })
})
