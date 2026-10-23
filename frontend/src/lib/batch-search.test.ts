import { describe, expect, it } from 'vitest'
import { maxBatchImageBytes, selectBatchFiles } from './batch-search'

const image = (name: string, size = 10, type = 'image/png', modified = 1) => ({ name, size, type, lastModified: modified }) as File

describe('batch query file selection', () => {
  it('deduplicates existing and newly selected files without collapsing distinct versions', () => {
    const existing = image('a.png')
    const result = selectBatchFiles([existing, image('b.png'), image('b.png'), image('a.png', 11), image('a.png', 10, 'image/png', 2)], [existing])
    expect(result.files).toHaveLength(3)
    expect(result.messages).toEqual(['已忽略 2 张重复图片。'])
  })

  it('rejects unsupported, empty and oversized images but accepts the exact size limit', () => {
    const valid = image('limit.png', maxBatchImageBytes)
    const result = selectBatchFiles([image('document.txt', 12, 'text/plain'), image('empty.png', 0), image('huge.png', maxBatchImageBytes + 1), valid], [])
    expect(result.files).toEqual([valid])
    expect(result.messages).toHaveLength(3)
    expect(result.messages.join(' ')).toContain('document.txt')
    expect(result.messages.join(' ')).toContain('huge.png')
  })

  it('limits the total queue and does not count invalid or duplicate files as queue slots', () => {
    const existing = Array.from({ length: 28 }, (_, index) => image(`${index}.png`))
    const result = selectBatchFiles([existing[0], image('bad.gif', 10, 'image/gif'), image('a.png'), image('b.png'), image('c.png')], existing)
    expect(result.files.map(file => file.name)).toEqual(['a.png', 'b.png'])
    expect(result.messages).toContain('每批最多 30 张，另有 1 张未加入。')
  })
})
