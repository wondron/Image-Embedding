/** Where the pickers of the annotation forms (upload, image edit, batch edit) keep what was picked last. */
export const ANNOTATION_RECENT_KEY = 'food-atlas-category-recent-picks'

const KEPT = 12

/**
 * Ids of the categories picked last in this browser, newest first. Empty when storage is
 * unavailable or holds something else; an id may no longer exist, so check it against the tree.
 */
export function loadRecentCategories(key: string): number[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(key) ?? '[]')
    if (!Array.isArray(stored)) return []
    return stored.filter((id): id is number => typeof id === 'number' && Number.isSafeInteger(id) && id > 0).slice(0, KEPT)
  } catch {
    return []
  }
}

/** Put a category first in the list; a write that fails is ignored (the pick itself already happened). */
export function rememberCategory(key: string, id: number): void {
  try {
    const next = [id, ...loadRecentCategories(key).filter(item => item !== id)].slice(0, KEPT)
    window.localStorage.setItem(key, JSON.stringify(next))
  } catch {
    /* nothing to remember */
  }
}
