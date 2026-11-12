import { defineConfig } from '../../frontend/node_modules/@playwright/test'

// QA ONLY. Isolated Vite on 127.0.0.1:57521. Does not start a webServer (caller starts it),
// does not install browsers, does not use box MCP Playwright, does not touch the user's :5173 service.
export default defineConfig({
  testDir: '.',
  testMatch: 'import-upload-layout.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 12_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: 'playwright-results.json' }],
  ],
  outputDir: 'playwright-output',
  use: {
    baseURL: 'http://127.0.0.1:57521',
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
