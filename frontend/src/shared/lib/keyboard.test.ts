import { describe, expect, it } from 'vitest'
import { isImeComposing } from './keyboard'

const key = (keyCode: number, isComposing: boolean) => ({ keyCode, nativeEvent: { isComposing } })

describe('isImeComposing', () => {
  it('knows the keys an input method is still using', () => {
    expect(isImeComposing(key(13, true))).toBe(true)    // Chrome / Firefox: Enter that commits 拼音
    expect(isImeComposing(key(229, false))).toBe(true)  // Safari: the committing Enter comes after compositionend
    expect(isImeComposing(key(229, true))).toBe(true)
  })

  it('leaves ordinary keys to the field', () => {
    expect(isImeComposing(key(13, false))).toBe(false)
    expect(isImeComposing(key(40, false))).toBe(false)
  })
})
