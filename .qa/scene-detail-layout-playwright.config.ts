import { defineConfig } from '../frontend/node_modules/@playwright/test'

// QA ONLY. Targets the already-running isolated Vite/fixture. Does not start a webServer,
// does not install browsers, and does not use MCP Playwright.
export default defineConfig({
  testDir: '.',
  testMatch: 'scene-detail-layout-gui.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: 'scene-detail-layout-playwright-results.json' }],
  ],
  outputDir: 'scene-detail-layout-playwright-output',
  use: {
    baseURL: 'http://127.0.0.1:57402',
    viewport: { width: 1440, height: 1000 },
    locale: 'zh-CN',
    timezoneId: 'America/Denver',
    colorScheme: 'light',
    screenshot: 'off',
    video: 'off',
    trace: 'off',
    actionTimeout: 10_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
