import { expect, type Page } from '@playwright/test'

/** The type of a notice (src/shared/ui/notice.ts). */
type NoticeType = 'success' | 'info' | 'warn' | 'error'

/** The popup of warnings and errors (StatusOverlay), while open. */
export function noticePopup(page: Page) {
  return page.locator('dialog.status-overlay[open]')
}

/** The light toast (success, info) saying `text`, wherever it shows (the body, or the open dialog). */
export function toast(page: Page, text: string | RegExp) {
  return page.locator('.so-toast').filter({ hasText: text })
}

/** A toast says `text` (and is of `type`). It does not block anything, so nothing has to close it. */
export async function expectToast(page: Page, text: string | RegExp, type?: Extract<NoticeType, 'success' | 'info'>) {
  const item = toast(page, text)
  await expect(item).toBeVisible()
  if (type) await expect(item).toHaveClass(new RegExp(`\\bis-${type}\\b`))
}

/**
 * The popup says `text` (and is of `type`); close it with 「知道了」 the way a person does. Hovering
 * first holds the countdown of one that would close by itself.
 */
export async function acknowledgeNotice(page: Page, text: string | RegExp, type?: Extract<NoticeType, 'warn' | 'error'>) {
  const popup = noticePopup(page)
  await expect(popup).toContainText(text)
  if (type) await expect(popup).toHaveClass(new RegExp(`\\bis-${type}\\b`))
  await popup.locator('.so-panel').hover()
  await popup.getByRole('button', { name: '知道了' }).click()
  await expect(popup.filter({ hasText: text })).toHaveCount(0)
}
