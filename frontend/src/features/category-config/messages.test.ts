import { describe, expect, it } from 'vitest'
import type { CategorySceneSync } from '../../entities/category'
import { savedNotice } from './messages'

const sync = (over: Partial<CategorySceneSync>): CategorySceneSync => ({
  images: 10, changed: 4, pushed: 4, pending: 0, vectorSync: 'pushed', ...over,
})

describe('savedNotice', () => {
  it('is the plain save when no image below the node changed scenes', () => {
    expect(savedNotice('已保存「披萨」。', null)).toEqual({ type: 'success', text: '已保存「披萨」。' })
    expect(savedNotice('已保存「披萨」。', sync({ changed: 0, pushed: 0 }))).toEqual({ type: 'success', text: '已保存「披萨」。' })
  })

  it('says how many images followed, and that search has them', () => {
    expect(savedNotice('已保存「披萨」。', sync({}))).toEqual({
      type: 'success', text: '已保存「披萨」。其下 4 张图片的场景归属已随之更新，并已同步到向量库。',
    })
  })

  it('warns when some are not in the vector store yet, with the reason', () => {
    const notReady = savedNotice('已保存「披萨」。', sync({ pushed: 0, pending: 4, vectorSync: 'not_ready' }))
    expect(notReady.type).toBe('warn')
    expect(notReady.title).toBe('部分图片还没同步到向量库')
    expect(notReady.text).toContain('其下 4 张图片的场景归属已随之更新，其中 4 张还没同步到向量库：检索服务未就绪')
    expect(savedNotice('已保存。', sync({ pushed: 3, pending: 1, vectorSync: 'failed' })).text).toContain('写入向量库失败，后台会自动重试')
    expect(savedNotice('已保存。', sync({ pushed: 3, pending: 1, vectorSync: 'partial' })).text).toContain('重新生成向量')
  })
})
