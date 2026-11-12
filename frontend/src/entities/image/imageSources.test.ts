import { afterEach, describe, expect, it } from 'vitest'
import { forgetBrokenHosts, isHostBroken, markHostBroken, storedImageSources, storedImageUrl } from './imageSources'

const ID = '0cf457e2-4a47-9f02-fd4d-34540389f720'

afterEach(forgetBrokenHosts)

describe('stored image sources', () => {
  it('is the API route of the stored file', () => {
    expect(storedImageUrl('', ID, 'thumb')).toBe(`/api/images/${ID}/file?variant=thumb`)
    expect(storedImageUrl('http://127.0.0.1:8320/', ID, 'original')).toBe(`http://127.0.0.1:8320/api/images/${ID}/file?variant=original`)
  })

  it("tries the image's own URL first, then the API's copy", () => {
    expect(storedImageSources(['https://cdn.example.local/t/a.webp'], ID, 'thumb', '')).toEqual([
      'https://cdn.example.local/t/a.webp',
      `/api/images/${ID}/file?variant=thumb`,
    ])
    // No URL: only the API; no id (not a library image): only the URL; neither: nothing
    expect(storedImageSources([null], ID, 'thumb', '')).toEqual([`/api/images/${ID}/file?variant=thumb`])
    expect(storedImageSources(['https://cdn/a.webp', undefined], null, 'thumb', '')).toEqual(['https://cdn/a.webp'])
    expect(storedImageSources([], null, 'thumb', '')).toEqual([])
  })

  it('skips the URLs of a host already seen failing', () => {
    markHostBroken('https://cdn.example.local/image-embedding/thumb/b.webp')
    expect(isHostBroken('https://cdn.example.local/other.png')).toBe(true)
    expect(isHostBroken('https://oss.example.com/a.png')).toBe(false)
    expect(storedImageSources(['https://cdn.example.local/t/a.webp', 'https://oss.example.com/a.png'], ID, 'original', ''))
      .toEqual(['https://oss.example.com/a.png', `/api/images/${ID}/file?variant=original`])
  })
})
