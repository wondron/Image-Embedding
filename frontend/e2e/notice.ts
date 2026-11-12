import { expect, type Page } from '@playwright/test'

/** The type of a popup (src/shared/ui/notice.ts). */
type NoticeType = 'success' | 'info' | 'warn' | 'error'

/** The app's one popup (StatusOverlay), while open. */
export function noticePopup(page: Page) {
  return page.locator('dialog.status-overlay[open]')
}

/**
 * The popup says `text` (and is of `type`); close it with 「知道了」 the way a person does. Hovering
 * first holds the countdown of one that would close by itself.
 */
export async function acknowledgeNotice(page: Page, text: string | RegExp, type?: NoticeType) {
  const popup = noticePopup(page)
  await expect(popup).toContainText(text)
  if (type) await expect(popup).toHaveClass(new RegExp(`\\bis-${type}\\b`))
  await popup.locator('.so-panel').hover()
  await popup.getByRole('button', { name: '知道了' }).click()
  await expect(popup.filter({ hasText: text })).toHaveCount(0)
}
