import { defineConfig } from '../frontend/node_modules/vitest/dist/config.js'
import { fileURLToPath } from 'node:url'
export default defineConfig({resolve:{alias:{react:fileURLToPath(new URL('../frontend/node_modules/react/index.js',import.meta.url))}},test:{environment:'node',include:['.qa/scene-completion-nongui.test.ts'],reporters:['json'],outputFile:'.qa/scene-completion-nongui-results.json'}})
