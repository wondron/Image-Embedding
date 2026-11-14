/** The parts of a keyboard event that say whether an input method editor owns the key. */
export interface ComposingKey {
  keyCode: number
  nativeEvent: { isComposing: boolean }
}

/**
 * The key belongs to an input method editor (拼音 and the like) that is composing text: Enter
 * there commits the candidate, the arrows choose among candidates, Escape drops the composition.
 * None of them is meant for the field's own shortcuts. Chrome and Firefox flag the event
 * (isComposing); Safari sends the committing Enter after compositionend, with only the IME
 * key code 229 to tell it apart.
 */
export function isImeComposing(event: ComposingKey): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}
