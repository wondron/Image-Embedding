import { fileURLToPath } from 'node:url'
export default {
 root: fileURLToPath(new URL('../frontend', import.meta.url)),
 resolve: { alias: { vitest: fileURLToPath(new URL('../frontend/node_modules/vitest/dist/index.js', import.meta.url)) } },
 test: { environment: 'node', include: ['../.qa/ingest-tasks-real-client.test.ts'] },
 esbuild: { jsx: 'automatic' }
}
