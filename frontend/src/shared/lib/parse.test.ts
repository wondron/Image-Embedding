import { describe, expect, it } from 'vitest'
import {
  asBoolean, asId, asInteger, asObject, asPositiveNumber, asText, asTextList, untrusted, untrustedList,
} from './parse'
import { safeHttpUrl, uniqueHttpUrls } from './safeHttpUrl'

describe('parse helpers', () => {
  it('reads text, ids and lists without inventing values', () => {
    expect(asText('  番茄 ')).toBe('番茄')
    for (const value of ['', '   ', 12, null, undefined, {}, []]) expect(asText(value)).toBeNull()
    expect(asId(' a1 ')).toBe('a1')
    expect(asId(42)).toBe('42')
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, '', null, {}]) expect(asId(value)).toBeNull()
    expect(asTextList([' general ', '', null, 3, 'oven'])).toEqual(['general', 'oven'])
    expect(asTextList('general')).toEqual([])
  })

  it('reads numbers and flags strictly', () => {
    expect(asPositiveNumber(640)).toBe(640)
    for (const value of [0, -1, '640', Number.NaN, null]) expect(asPositiveNumber(value)).toBeNull()
    expect(asInteger(3)).toBe(3)
    for (const value of [3.5, '3', null]) expect(asInteger(value)).toBeNull()
    expect(asBoolean(false)).toBe(false)
    for (const value of ['true', 0, null, undefined]) expect(asBoolean(value)).toBeUndefined()
  })

  it('keeps only plain objects', () => {
    expect(asObject({ a: 1 })).toEqual({ a: 1 })
    for (const value of [null, [], 'x', 1]) expect(asObject(value)).toBeNull()
    expect(untrusted<{ id: string }>(['x'])).toBeNull()
    expect(untrustedList<{ id: string }>([{ id: 'a' }, null, 'bad', [], 1, { id: 'b' }])).toEqual([{ id: 'a' }, { id: 'b' }])
    expect(untrustedList({ id: 'a' })).toEqual([])
  })
})

describe('safeHttpUrl', () => {
  it('accepts HTTP(S) and rejects unsafe URLs, embedded credentials and malformed values', () => {
    expect(safeHttpUrl(' https://example.com/a.png ')).toBe('https://example.com/a.png')
    expect(safeHttpUrl('http://localhost:8080/a')).toBe('http://localhost:8080/a')
    expect(safeHttpUrl('//cdn.example/a.png')).toBe('https://cdn.example/a.png')
    for (const value of [null, {}, '', '/local', 'bad-url', 'javascript:alert(1)', 'data:image/png;base64,abc', 'file:///c:/a.png', 'https://user:password@example.com/a']) {
      expect(safeHttpUrl(value)).toBeNull()
    }
  })

  it('keeps order and drops unsafe or repeated URLs', () => {
    expect(uniqueHttpUrls(['https://a.example/1.png', 'javascript:alert(1)', null, '//a.example/1.png', 'https://b.example/2.png']))
      .toEqual(['https://a.example/1.png', 'https://b.example/2.png'])
  })
})
