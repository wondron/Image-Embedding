// Run the real component's event handlers with hooks/JSX stubbed, without a browser or API.
const fs = require('fs');
const path = require('path');
const Module = require('module');
const esbuild = require('../frontend/node_modules/esbuild');
const originalLoad = Module._load;
const hookStub = {
  useState: value => [typeof value === 'function' ? value() : value, () => {}],
  useRef: value => ({ current: value }),
  useMemo: fn => fn(),
  useId: () => 'test-id',
  useEffect: () => {},
  useLayoutEffect: () => {},
};
const jsx = (type, props) => ({ type, props });
Module._load = function (request, parent, isMain) {
  if (request === 'react') return hookStub;
  if (request === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
  if (request === 'lucide-react') return new Proxy({}, { get: (_, key) => key });
  if (request.endsWith('entities/category')) return originalLoad.call(this, path.resolve(__dirname, '../frontend/src/entities/category/model.ts'), parent, isMain);
  return originalLoad.call(this, request, parent, isMain);
};
for (const ext of ['.ts', '.tsx']) {
  Module._extensions[ext] = (module, filename) => module._compile(esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {
    loader: ext === '.tsx' ? 'tsx' : 'ts', format: 'cjs', jsx: 'automatic', target: 'node22',
  }).code, filename);
}
Module._extensions['.css'] = () => {};
const { CategorySearch } = require('../frontend/src/features/category-picker/CategorySearch.tsx');
const { buildCategoryTree } = require('../frontend/src/entities/category/model.ts');
const tree = buildCategoryTree([{ id: 1, code: 'ingredient', name: '食材', names: [], parentId: null, isActive: true }]);
const find = (element, type) => {
  if (!element || typeof element !== 'object') return undefined;
  if (element?.type === type) return element;
  const children = element?.props?.children;
  for (const child of Array.isArray(children) ? children.flat(Infinity) : [children]) {
    const found = find(child, type);
    if (found) return found;
  }
};
const picked = [];
const render = disabled => CategorySearch({ id: 'category', label: '类别', tree, value: undefined, onPick: id => picked.push(id), alwaysOpen: true, disabled });
let prevented = false;
find(render(false), 'input').props.onKeyDown({ key: 'Enter', nativeEvent: { isComposing: true }, isComposing: true, preventDefault: () => { prevented = true; } });
const ime = { picked: [...picked], prevented };
picked.length = 0;
find(render(true), 'li').props.onClick();
const disabledClick = { picked: [...picked] };
console.log(JSON.stringify({ ime, disabledClick }, null, 2));
