# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 空库：先建类别域，再在它下面建下级类别
- Location: e2e\category-config.spec.ts:215:1

# Error details

```
Error: expect(locator).toHaveValue(expected) failed

Locator:  getByRole('dialog', { name: '新建类别' }).locator('#category-form-parentId')
Expected: "root"
Received: ""
Timeout:  7000ms

Call log:
  - Expect "toHaveValue" getByRole('dialog', { name: '新建类别' }).locator('#category-form-parentId') with timeout 7000ms
  - waiting for getByRole('dialog', { name: '新建类别' }).locator('#category-form-parentId')
    18 × locator resolved to <input value="" role="combobox" autocomplete="off" aria-haspopup="tree" aria-expanded="false" aria-controls="_r_5_" placeholder="搜索以更换上级类别" aria-autocomplete="list" id="category-form-parentId" aria-describedby="category-form-parentId-hint"/>
       - unexpected value ""

```

```yaml
- combobox "上级类别"
```

# Test source

```ts
  121 |         walk(target, code)
  122 |       }
  123 |       const codes = this.rows.map(item => plan.get(item.id) ?? item.code).map(value => value.toLowerCase())
  124 |       if (new Set(codes).size !== codes.length) {
  125 |         return route.fulfill({ status: 409, json: errorBody('CATEGORY_CODE_EXISTS', 'another category already uses a planned subtree code') })
  126 |       }
  127 |       Object.assign(target, body)
  128 |       for (const item of this.rows) if (plan.has(item.id)) item.code = plan.get(item.id)!
  129 |       const sceneSync = this.sceneSync
  130 |       this.sceneSync = null
  131 |       return route.fulfill({ json: { ok: true, category: this.item(target), scene_sync: sceneSync } })
  132 |     }
  133 |     if (method === 'DELETE') {
  134 |       if (this.rows.some(r => r.parent_id === target.id) || target.image_count > 0 || target.scene_rule_count > 0) {
  135 |         return route.fulfill({ status: 409, json: errorBody('CATEGORY_IN_USE', `category '${target.code}' is still in use`) })
  136 |       }
  137 |       this.rows = this.rows.filter(r => r !== target)
  138 |       return route.fulfill({ json: { ok: true, deleted: { id: target.id, code: target.code } } })
  139 |     }
  140 |     return route.abort('blockedbyclient')
  141 |   }
  142 | 
  143 |   writes(): Call[] {
  144 |     return this.calls.filter(call => call.method !== 'GET')
  145 |   }
  146 | }
  147 | 
  148 | async function open(page: Page, rows: Row[], url = '/?view=categories'): Promise<Backend> {
  149 |   const backend = new Backend(rows)
  150 |   // Match the path prefix, not a '**/api/**' glob: that glob also catches Vite's /src/shared/api/* modules.
  151 |   await page.route(url_ => url_.pathname.startsWith('/api/'), route => {
  152 |     const path = new URL(route.request().url()).pathname
  153 |     if (path === '/api/ping') return route.fulfill({ json: { ok: true, max_image_bytes: 15 * 1024 * 1024 } })
  154 |     if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [DOMAIN_ENUM] } })
  155 |     if (path.startsWith('/api/categories')) return backend.handle(route)
  156 |     return route.abort('blockedbyclient')
  157 |   })
  158 |   await page.goto(url)
  159 |   return backend
  160 | }
  161 | 
  162 | const table = (page: Page) => page.getByRole('region', { name: '类别列表' })
  163 | /** A node's name button (it opens the editor); its name is the label, then the code. */
  164 | const nodeButton = (page: Page, label: string, code: string) => table(page).getByRole('button', {
  165 |   name: new RegExp(`^${label}\\s*(停用\\s*)?${code.replace(/\./g, '\\.')}$`),
  166 | })
  167 | const rowOf = (page: Page, id: number) => page.locator(`#category-row-${id}`)
  168 | const createButton = (page: Page) => page.getByRole('button', { name: '新建类别', exact: true })
  169 | const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)
  170 | 
  171 | test('导航只有「食材管理」一个入口，它就是类别树', async ({ page }) => {
  172 |   await open(page, seededRows(), '/')
  173 |   const nav = page.getByRole('navigation', { name: '主导航' })
  174 |   await expect(nav.getByRole('button', { name: '类别配置' })).toHaveCount(0)
  175 |   await nav.getByRole('button', { name: '食材管理' }).click()
  176 |   await expect(page).toHaveURL(/[?&]view=categories/)
  177 |   await expect(page.getByRole('heading', { level: 1, name: '食材管理' })).toBeVisible()
  178 |   await expect(nodeButton(page, '披萨', 'dish.pizza')).toBeVisible() // roots are open after the first load
  179 | })
  180 | 
  181 | test('五个根类别后可以添加「其他」，满六个后只允许添加下级类别', async ({ page }) => {
  182 |   const roots = DOMAIN_ENUM.values.filter(value => value.value !== 'other')
  183 |     .map((value, index) => row(index + 1, value.value, null, value.label))
  184 |   const backend = await open(page, roots)
  185 |   await expect(nodeButton(page, '混合', 'mixed')).toBeVisible()
  186 |   await createButton(page).click()
  187 |   const dialog = page.getByRole('dialog', { name: '新建类别' })
  188 |   await dialog.locator('#category-form-parentId').selectOption('root')
  189 |   await expect(dialog.locator('#category-form-code option')).toHaveText(['其他（other）'])
  190 |   await expect(dialog.locator('#category-form-zhName')).toHaveValue('其他')
  191 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  192 |   await expect(dialog).toBeHidden()
  193 |   await expect(nodeButton(page, '其他', 'other')).toBeVisible()
  194 |   expect(backend.rows.filter(item => item.parent_id === null)).toHaveLength(6)
  195 |   expect(backend.writes()).toHaveLength(1)
  196 |   expect(backend.writes()[0].body).toMatchObject({ code: 'other', parent_id: null, names: [zh('其他')] })
  197 | 
  198 |   await createButton(page).click()
  199 |   await expect(dialog.locator('#category-form-parentId option[value="root"]')).toHaveCount(0)
  200 |   await expect(dialog.locator('#category-form-parentId-hint')).toContainText('已达到六个根类别的上限')
  201 |   await dialog.locator('#category-form-parentId').selectOption('6')
  202 |   await expect(dialog.locator('#category-form-code')).toHaveValue('other.')
  203 | })
  204 | 
  205 | test('已有六个根类别包含旧编码时，也不再提供第七个根类别', async ({ page }) => {
  206 |   const roots = DOMAIN_ENUM.values.filter(value => value.value !== 'other')
  207 |     .map((value, index) => row(index + 1, value.value, null, value.label))
  208 |   await open(page, [...roots, row(6, 'legacy', null, '旧类别域')])
  209 |   await createButton(page).click()
  210 |   const dialog = page.getByRole('dialog', { name: '新建类别' })
  211 |   await expect(dialog.locator('#category-form-parentId option[value="root"]')).toHaveCount(0)
  212 |   await expect(dialog.locator('#category-form-parentId-hint')).toContainText('已达到六个根类别的上限')
  213 | })
  214 | 
  215 | test('空库：先建类别域，再在它下面建下级类别', async ({ page }) => {
  216 |   const backend = await open(page, [])
  217 |   await expect(table(page).getByRole('heading', { name: '还没有类别' })).toBeVisible()
  218 | 
  219 |   await createButton(page).click()
  220 |   const rootDialog = page.getByRole('dialog', { name: '新建类别' })
> 221 |   await expect(rootDialog.locator('#category-form-parentId')).toHaveValue('root')
      |                                                               ^ Error: expect(locator).toHaveValue(expected) failed
  222 |   // The root codes come from the API; their Chinese names from the category_domain enum.
  223 |   await expect(rootDialog.locator('#category-form-code option')).toHaveText(['食材（ingredient）', '菜品（dish）', '甜点烘焙（dessert）', '饮品（beverage）', '混合（mixed）', '其他（other）'])
  224 |   await expect(rootDialog.locator('#category-form-zhName')).toHaveValue('食材')
  225 |   await rootDialog.locator('#category-form-code').selectOption('dish')
  226 |   await expect(rootDialog.locator('#category-form-zhName')).toHaveValue('菜品')
  227 |   await rootDialog.getByRole('button', { name: '创建', exact: true }).click()
  228 |   await expect(rootDialog).toBeHidden()
  229 |   await expectToast(page, '已创建「菜品」。', 'success')
  230 |   await expect(nodeButton(page, '菜品', 'dish')).toHaveAttribute('aria-current', 'true')
  231 |   expect(backend.writes()[0]).toEqual({
  232 |     method: 'POST',
  233 |     path: '/api/categories',
  234 |     body: { code: 'dish', parent_id: null, names: [zh('菜品')], cuisines: [], heating_group_id: null, sort_order: 0, is_active: true },
  235 |   })
  236 | 
  237 |   await page.getByRole('button', { name: '新建「菜品」的下级类别' }).click()
  238 |   const childDialog = page.getByRole('dialog', { name: '新建类别' })
  239 |   await expect(childDialog.locator('#category-form-parentId')).toHaveValue('1')
  240 |   const code = childDialog.locator('#category-form-code')
  241 |   await expect(code).toHaveValue('dish.')
  242 |   await expect(code).toBeFocused()
  243 |   await page.keyboard.type('pizza') // the caret is after the prefix
  244 |   await childDialog.locator('#category-form-zhName').fill('披萨')
  245 |   await childDialog.getByRole('button', { name: '添加名称' }).click()
  246 |   await childDialog.getByLabel('第 1 行语言代码', { exact: true }).fill('EN') // typed upper-case, stored lower-case
  247 |   await childDialog.getByLabel('第 1 行名称', { exact: true }).fill('Pizza')
  248 |   await childDialog.getByLabel('第 1 行类型', { exact: true }).selectOption('primary')
  249 |   await childDialog.locator('#category-form-cuisines').fill('italian， american')
  250 |   await childDialog.locator('#category-form-heatingGroupId').selectOption('2')
  251 |   await childDialog.locator('#category-form-sortOrder').fill('3')
  252 |   await childDialog.getByRole('button', { name: '创建', exact: true }).click()
  253 |   await expect(childDialog).toBeHidden()
  254 |   expect(backend.writes()[1].body).toEqual({
  255 |     code: 'dish.pizza',
  256 |     parent_id: 1,
  257 |     names: [zh('披萨'), { lang: 'en', name: 'Pizza', kind: 'primary' }],
  258 |     cuisines: ['italian', 'american'],
  259 |     heating_group_id: 2,
  260 |     sort_order: 3,
  261 |     is_active: true,
  262 |   })
  263 | 
  264 |   // The new row is highlighted under its opened parent and shows what was stored.
  265 |   await expect(nodeButton(page, '披萨', 'dish.pizza')).toHaveAttribute('aria-current', 'true')
  266 |   await expect(page.getByRole('button', { name: '收起「菜品」' })).toBeVisible()
  267 |   await expect(rowOf(page, 2)).toContainText('Pizza')
  268 |   await expect(rowOf(page, 2)).toContainText('italian')
  269 |   await expect(rowOf(page, 2)).toContainText('american')
  270 |   await expect(rowOf(page, 2)).toContainText('烤')
  271 |   await expect(page).toHaveURL(/[?&]view=categories&category=2$/)
  272 | })
  273 | 
  274 | test('工具栏新建：先选上级，编码随上级前缀变化', async ({ page }) => {
  275 |   const backend = await open(page, seededRows())
  276 |   await createButton(page).click()
  277 |   const dialog = page.getByRole('dialog', { name: '新建类别' })
  278 |   const parent = dialog.locator('#category-form-parentId')
  279 |   const code = dialog.locator('#category-form-code')
  280 |   await expect(parent).toBeFocused()
  281 |   await expect(code).toBeDisabled()
  282 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  283 |   await expect(dialog.locator('#category-form-parentId-error')).toHaveText('请选择上级类别。')
  284 | 
  285 |   await parent.selectOption('2') // 披萨
  286 |   await expect(code).toHaveValue('dish.pizza.')
  287 |   await parent.focus()
  288 |   await page.keyboard.press('Tab') // Tab selects a field's whole value; the prefix must survive typing
  289 |   await expect(code).toBeFocused()
  290 |   await page.keyboard.type('calzone')
  291 |   await expect(code).toHaveValue('dish.pizza.calzone')
  292 |   await parent.selectOption('5') // 食材: what was typed moves under the new prefix
  293 |   await expect(code).toHaveValue('ingredient.calzone')
  294 | 
  295 |   // The four unused domain codes are still free for a root; the name follows the domain.
  296 |   await parent.selectOption('root')
  297 |   await expect(code.locator('option')).toHaveText(['甜点烘焙（dessert）', '饮品（beverage）', '混合（mixed）', '其他（other）'])
  298 |   await expect(dialog.locator('#category-form-zhName')).toHaveValue('甜点烘焙')
  299 |   await parent.selectOption('3') // 面条
  300 |   await expect(code).toHaveValue('dish.noodle.')
  301 |   await expect(dialog.locator('#category-form-zhName')).toHaveValue('')
  302 | 
  303 |   // Like a person: fill() selects before focusing, and focusing a prefix-only code moves the caret to its end
  304 |   await code.click()
  305 |   await page.keyboard.press('ControlOrMeta+A')
  306 |   await page.keyboard.type('dish.noodle.fried')
  307 |   await dialog.locator('#category-form-zhName').fill('炒面')
  308 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  309 |   await expect(dialog).toBeHidden()
  310 |   expect(backend.writes()).toEqual([{
  311 |     method: 'POST',
  312 |     path: '/api/categories',
  313 |     body: { code: 'dish.noodle.fried', parent_id: 3, names: [zh('炒面')], cuisines: [], heating_group_id: null, sort_order: 0, is_active: true },
  314 |   }])
  315 |   await expect(nodeButton(page, '炒面', 'dish.noodle.fried')).toHaveAttribute('aria-current', 'true') // 面条 opened to show it
  316 | })
  317 | 
  318 | test('编辑只提交改过的字段；换上级时下级类别一起移动', async ({ page }) => {
  319 |   const backend = await open(page, seededRows())
  320 |   await expect(rowOf(page, 2)).toContainText('italian')
  321 |   await page.getByRole('button', { name: '编辑「披萨」' }).click()
```