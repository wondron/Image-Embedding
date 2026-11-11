import { fileURLToPath } from 'node:url'
export default {
 resolve: { alias: { vitest: fileURLToPath(new URL('../frontend/node_modules/vitest/dist/index.js', import.meta.url)), react: fileURLToPath(new URL('../frontend/node_modules/react', import.meta.url)) } },
 test: { globals: true, environment: 'node', include: ['.qa/ingest-tasks-qa.*.test.{ts,tsx}'] },
 esbuild: { jsx: 'automatic' }
}
