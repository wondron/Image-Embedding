import base from '../frontend/playwright.config'
export default {
  ...base,
  testDir: '../frontend/e2e',
  outputDir: './bugcheck-20261113-frontend-results',
  use: { ...base.use, channel: undefined },
  webServer: { ...base.webServer, cwd: 'C:/Users/47202/PROJECT/Image-Embedding/frontend' },
}
