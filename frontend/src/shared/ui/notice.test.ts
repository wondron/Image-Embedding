import { describe, expect, it, vi } from 'vitest'
import { NOTICE_DURATION, NOTICE_QUEUE_LIMIT, createNoticeStore } from './notice'

describe('notice store', () => {
  it('fills in the title, the duration and the backdrop rule by type', () => {
    const store = createNoticeStore()
    store.emit({ type: 'success', message: '已保存。' })
    expect(store.current()).toMatchObject({ type: 'success', title: '操作成功', message: '已保存。', duration: NOTICE_DURATION.success, closable: true, details: [], action: null })
    store.emit({ type: 'error', message: '保存失败', title: '  ' })
    expect(store.current()).toMatchObject({ title: '操作失败', duration: 0 })
    const plain = createNoticeStore()
    plain.emit({ message: '提示一下', duration: -5, closable: false })
    expect(plain.current()).toMatchObject({ type: 'info', title: '提示', duration: 0, closable: false })
  })

  it('lets a newer popup replace one that closes by itself', () => {
    const store = createNoticeStore()
    store.emit({ type: 'success', message: 'A' })
    store.emit({ type: 'info', message: 'B' })
    expect(store.current()?.message).toBe('B')
    expect(store.pending()).toEqual([])
  })

  it('never replaces a popup that stays: the newer ones wait behind it, in order', () => {
    const store = createNoticeStore()
    store.emit({ type: 'error', message: '失败' })
    store.emit({ type: 'success', message: '后来成功' })
    store.emit({ type: 'warn', message: '注意' })
    expect(store.current()?.message).toBe('失败')
    expect(store.pending().map(notice => notice.message)).toEqual(['后来成功', '注意'])
    store.close()
    expect(store.current()?.message).toBe('后来成功')
    store.close()
    expect(store.current()?.message).toBe('注意')
    store.close()
    expect(store.current()).toBeNull()
  })

  it('shows the same message once, starting its countdown over', () => {
    const store = createNoticeStore()
    const first = store.emit({ type: 'success', message: '已保存。' })
    const second = store.emit({ type: 'success', message: '已保存。' })
    expect(second).not.toBe(first)
    expect(store.current()?.id).toBe(second)
    store.emit({ type: 'error', message: '失败' })
    store.emit({ type: 'info', message: '稍后' })
    const waiting = store.pending()[0].id
    expect(store.emit({ type: 'info', message: '稍后' })).toBe(waiting)
    expect(store.pending()).toHaveLength(1)
  })

  it('closes by id: a stale timer does not close a newer popup', () => {
    const store = createNoticeStore()
    const old = store.emit({ type: 'success', message: 'A' })
    store.emit({ type: 'success', message: 'B' })
    store.close(old)
    expect(store.current()?.message).toBe('B')
    store.emit({ type: 'error', message: 'E' })
    const queued = store.emit({ type: 'info', message: 'Q' })
    store.close(queued)
    expect(store.pending()).toEqual([])
    expect(store.current()?.message).toBe('E')
  })

  it('keeps only the newest waiting popups', () => {
    const store = createNoticeStore()
    store.emit({ type: 'error', message: '失败' })
    for (let index = 0; index < NOTICE_QUEUE_LIMIT + 3; index += 1) store.emit({ type: 'info', message: `第 ${index} 条` })
    expect(store.pending()).toHaveLength(NOTICE_QUEUE_LIMIT)
    expect(store.pending()[0].message).toBe('第 3 条')
  })

  it('tells its subscribers about every change, and stops when they leave', () => {
    const store = createNoticeStore()
    const listener = vi.fn()
    const leave = store.subscribe(listener)
    store.emit({ type: 'info', message: 'A' })
    store.close()
    store.close()  // nothing showing: no change, no call
    expect(listener).toHaveBeenCalledTimes(2)
    store.emit({ type: 'error', message: 'B' })
    store.emit({ type: 'info', message: 'C' })
    store.clear()
    expect([store.current(), store.pending()]).toEqual([null, []])
    leave()
    store.emit({ type: 'info', message: 'D' })
    expect(listener).toHaveBeenCalledTimes(5)
  })
})
