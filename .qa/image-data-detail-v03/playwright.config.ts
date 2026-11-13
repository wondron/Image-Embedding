import { defineConfig } from '../../frontend/node_modules/@playwright/test'

// QA ONLY. Isolated Vite on 127.0.0.1:57611. Caller starts the server.
// Does not install browsers, does not use box Playwright, does not touch :5173.
export default defineConfig({
  testDir: '.',
  testMatch: 'image-data-detail-v03.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 12_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: 'playwright-results.json' }],
  ],
  outputDir: 'playwright-output',
  use: {
    baseURL: 'http://127.0.0.1:57611',
    locale: 'zh-CN',
    timezoneId: 'America/Denver',
    colorScheme: 'light',
    screenshot: 'off',
    video: 'off',
    trace: 'off',
    actionTimeout: 12_000,
    headless: false,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
