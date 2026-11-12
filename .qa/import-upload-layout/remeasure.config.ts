import { defineConfig } from '../../frontend/node_modules/@playwright/test'
export default defineConfig({
  testDir: '.',
  testMatch: 'remeasure-i5-i6.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:57521',
    locale: 'zh-CN',
    timezoneId: 'America/Denver',
    colorScheme: 'light',
    headless: false,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
