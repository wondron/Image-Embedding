# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 五个根类别后可以添加「其他」，满六个后只允许添加下级类别
- Location: e2e\category-config.spec.ts:181:1

# Error details

```
Error: locator.selectOption: Error: Element is not a <select> element
Call log:
  - waiting for getByRole('dialog', { name: '新建类别' }).locator('#category-form-parentId')
    - locator resolved to <input value="" role="combobox" autocomplete="off" aria-expanded="true" aria-haspopup="tree" aria-controls="_r_5_" aria-autocomplete="list" id="category-form-parentId" data-dialog-initial-focus="true" placeholder="搜索名称、编码或别名，或在下面逐级展开" aria-activedescendant="_r_5_-none" aria-describedby="category-form-parentId-hint"/>
  - attempting select option action
    - waiting for element to be visible and enabled

```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e3]:
    - complementary [ref=e4]:
      - generic [ref=e5]:
        - generic [ref=e6]: 导航
        - button "收起侧栏" [expanded] [ref=e7] [cursor=pointer]
      - navigation "主导航" [ref=e11]:
        - generic [ref=e12]: 图像检索
        - button "图搜图" [ref=e14] [cursor=pointer]
        - generic [ref=e22]:
          - generic [ref=e23]: 数据管理
          - button "图片数据" [ref=e24] [cursor=pointer]
        - button "上传与导入" [ref=e33] [cursor=pointer]
        - button "入库任务" [ref=e40] [cursor=pointer]
        - button "食材管理" [ref=e48] [cursor=pointer]
        - button "场景管理" [ref=e56] [cursor=pointer]
        - button "枚举配置" [ref=e64] [cursor=pointer]
        - button "模型版本" [ref=e71] [cursor=pointer]
      - generic [ref=e77]:
        - button "搜索服务可连接" [ref=e78] [cursor=pointer]
        - generic [ref=e90]:
          - text: 图像管理工作台
          - generic [ref=e91]: 前端预览 · v0.1
    - generic [ref=e92]:
      - banner [ref=e93]:
        - generic [ref=e95]:
          - heading "食材管理" [level=1] [ref=e96]
          - generic [ref=e97]: 食材图像管理 / 类别树：名称、别名与属性
        - generic [ref=e98]:
          - button "使用说明" [ref=e99] [cursor=pointer]
          - button "连接设置" [ref=e103] [cursor=pointer]
      - main [ref=e107]:
        - generic [ref=e108]:
          - generic [ref=e109]:
            - textbox "搜索类别" [ref=e114]:
              - /placeholder: 搜索名称、别名或编码…
            - combobox "范围筛选" [ref=e115]:
              - option "全部类别" [selected]
              - option "食材"
              - option "菜品"
              - option "甜点烘焙"
              - option "饮品"
              - option "混合"
            - combobox "状态筛选" [ref=e116]:
              - option "全部状态" [selected]
              - option "仅启用"
              - option "仅停用"
            - button "新建类别" [ref=e117] [cursor=pointer]
            - generic [ref=e119]: 类别树已接入
          - region "类别列表" [ref=e121]:
            - generic [ref=e122]:
              - generic [ref=e123]:
                - generic [ref=e124]: 名称 / 编码
                - generic [ref=e125]: 其他名称
                - generic [ref=e126]: 菜系
                - generic [ref=e127]: 加热组
                - generic [ref=e128]: 使用
                - generic [ref=e129]: 状态
                - generic [ref=e130]: 操作
              - list [ref=e131]:
                - listitem [ref=e132]:
                  - generic [ref=e133]:
                    - button [ref=e139] [cursor=pointer]:
                      - strong [ref=e141]: 食材
                      - code [ref=e142]: ingredient
                    - generic [ref=e143]: —
                    - generic [ref=e144]: —
                    - generic [ref=e145]: —
                    - generic [ref=e146]: —
                    - generic [ref=e147]: 启用
                    - generic [ref=e149]:
                      - button "新建「食材」的下级类别" [ref=e150] [cursor=pointer]
                      - button "编辑「食材」" [ref=e152] [cursor=pointer]
                      - button "删除「食材」" [ref=e156] [cursor=pointer]
                - listitem [ref=e160]:
                  - generic [ref=e161]:
                    - button [ref=e167] [cursor=pointer]:
                      - strong [ref=e169]: 菜品
                      - code [ref=e170]: dish
                    - generic [ref=e171]: —
                    - generic [ref=e172]: —
                    - generic [ref=e173]: —
                    - generic [ref=e174]: —
                    - generic [ref=e175]: 启用
                    - generic [ref=e177]:
                      - button "新建「菜品」的下级类别" [ref=e178] [cursor=pointer]
                      - button "编辑「菜品」" [ref=e180] [cursor=pointer]
                      - button "删除「菜品」" [ref=e184] [cursor=pointer]
                - listitem [ref=e188]:
                  - generic [ref=e189]:
                    - button [ref=e195] [cursor=pointer]:
                      - strong [ref=e197]: 甜点烘焙
                      - code [ref=e198]: dessert
                    - generic [ref=e199]: —
                    - generic [ref=e200]: —
                    - generic [ref=e201]: —
                    - generic [ref=e202]: —
                    - generic [ref=e203]: 启用
                    - generic [ref=e205]:
                      - button "新建「甜点烘焙」的下级类别" [ref=e206] [cursor=pointer]
                      - button "编辑「甜点烘焙」" [ref=e208] [cursor=pointer]
                      - button "删除「甜点烘焙」" [ref=e212] [cursor=pointer]
                - listitem [ref=e216]:
                  - generic [ref=e217]:
                    - button [ref=e223] [cursor=pointer]:
                      - strong [ref=e225]: 饮品
                      - code [ref=e226]: beverage
                    - generic [ref=e227]: —
                    - generic [ref=e228]: —
                    - generic [ref=e229]: —
                    - generic [ref=e230]: —
                    - generic [ref=e231]: 启用
                    - generic [ref=e233]:
                      - button "新建「饮品」的下级类别" [ref=e234] [cursor=pointer]
                      - button "编辑「饮品」" [ref=e236] [cursor=pointer]
                      - button "删除「饮品」" [ref=e240] [cursor=pointer]
                - listitem [ref=e244]:
                  - generic [ref=e245]:
                    - button [ref=e251] [cursor=pointer]:
                      - strong [ref=e253]: 混合
                      - code [ref=e254]: mixed
                    - generic [ref=e255]: —
                    - generic [ref=e256]: —
                    - generic [ref=e257]: —
                    - generic [ref=e258]: —
                    - generic [ref=e259]: 启用
                    - generic [ref=e261]:
                      - button "新建「混合」的下级类别" [ref=e262] [cursor=pointer]
                      - button "编辑「混合」" [ref=e264] [cursor=pointer]
                      - button "删除「混合」" [ref=e268] [cursor=pointer]
            - generic [ref=e272]:
              - generic [ref=e273]: 共 5 个类别
              - generic [ref=e274]:
                - button "全部展开" [ref=e275] [cursor=pointer]
                - button "全部收起" [ref=e276] [cursor=pointer]
                - button "刷新" [ref=e277] [cursor=pointer]
          - paragraph [ref=e281]: 这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，标在它和下级类别上的图片会随即重算场景归属，并同步到向量库。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。
          - dialog [ref=e282]:
            - generic [ref=e283]:
              - heading "新建类别" [level=2] [ref=e284]
              - button "关闭弹窗" [ref=e285] [cursor=pointer]
            - generic [ref=e290]:
              - generic [ref=e291]:
                - region [ref=e292]:
                  - heading "位置与编码" [level=3] [ref=e293]
                  - generic [ref=e294]:
                    - generic [ref=e295]: 上级类别 *
                    - generic [ref=e297]:
                      - combobox "上级类别" [expanded] [active] [ref=e302]
                      - tree "上级类别候选" [ref=e303]:
                        - treeitem "（根节点 · 类别域）" [level=1] [ref=e304] [cursor=pointer]
                        - treeitem "食材" [level=1] [ref=e307] [cursor=pointer]
                        - treeitem "菜品" [level=1] [ref=e310] [cursor=pointer]
                        - treeitem "甜点烘焙" [level=1] [ref=e313] [cursor=pointer]
                        - treeitem "饮品" [level=1] [ref=e316] [cursor=pointer]
                        - treeitem "混合" [level=1] [ref=e319] [cursor=pointer]
                    - generic [ref=e322]: 新类别放在哪个类别下面；只有类别域（食材、菜品等）可以放在最顶层。
                  - generic [ref=e323]:
                    - generic [ref=e324]: 编码 *
                    - textbox "编码 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。" [disabled] [ref=e325]:
                      - /placeholder: 先选择上级类别
                    - generic [ref=e326]: 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。
                - region [ref=e327]:
                  - heading "名称" [level=3] [ref=e328]
                  - generic [ref=e329]:
                    - generic [ref=e330]: 中文名称 *
                    - textbox "中文名称 检索结果和图库里显示的类别名。" [ref=e331]
                    - generic [ref=e332]: 检索结果和图库里显示的类别名。
                  - group "其他名称" [ref=e333]:
                    - button "添加名称" [ref=e336] [cursor=pointer]
                    - generic [ref=e338]: 中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名；搜索时别名也能找到它。
                - region [ref=e339]:
                  - heading "属性" [level=3] [ref=e340]
                  - generic [ref=e341]:
                    - generic [ref=e342]: 菜系
                    - textbox "菜系 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。" [ref=e343]:
                      - /placeholder: chinese, american
                    - generic [ref=e344]: 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。
                  - generic [ref=e345]:
                    - generic [ref=e346]: 加热组
                    - combobox "加热组 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。" [ref=e347]:
                      - option "不设置" [selected]
                      - option "烤（grill）"
                    - generic [ref=e348]: 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。
                  - generic [ref=e349]:
                    - generic [ref=e350]: 同级排序
                    - spinbutton "同级排序 整数，同一上级下数字小的排在前面。" [ref=e351]: "0"
                    - generic [ref=e352]: 整数，同一上级下数字小的排在前面。
                  - generic [ref=e353]:
                    - generic [ref=e354]: 状态
                    - generic [ref=e355] [cursor=pointer]:
                      - checkbox "启用" [checked] [ref=e356]
                      - text: 启用
                    - generic [ref=e357]: 停用后上传和图库编辑里不再列出它；它的图片和下级类别不受影响。
              - generic [ref=e358]:
                - button "取消" [ref=e359] [cursor=pointer]
                - button "创建" [ref=e360] [cursor=pointer]
  - status
```

# Test source

```ts
  88  |   }
  89  | 
  90  |   async handle(route: Route): Promise<void> {
  91  |     const request = route.request()
  92  |     const method = request.method()
  93  |     const path = new URL(request.url()).pathname
  94  |     const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : undefined
  95  |     this.calls.push({ method, path, body })
  96  |     if (method === 'GET' && path === '/api/categories') {
  97  |       return route.fulfill(this.listFailure ?? { json: this.list() })
  98  |     }
  99  |     if (method === 'POST' && path === '/api/categories') {
  100 |       const input = body as Omit<Row, 'id' | 'image_count' | 'scene_rule_count'>
  101 |       if (this.rows.some(r => r.code.toLowerCase() === input.code.toLowerCase())) {
  102 |         return route.fulfill({ status: 409, json: errorBody('CATEGORY_CODE_EXISTS', `another category already uses the code '${input.code}'`) })
  103 |       }
  104 |       const created: Row = { ...input, id: Math.max(0, ...this.rows.map(r => r.id)) + 1, image_count: 0, scene_rule_count: 0 }
  105 |       this.rows.push(created)
  106 |       return route.fulfill({ status: 201, json: { ok: true, category: this.item(created) } })
  107 |     }
  108 |     const target = this.rows.find(r => `/api/categories/${r.id}` === path)
  109 |     if (!target) return route.fulfill({ status: 404, json: errorBody('CATEGORY_NOT_FOUND', 'category does not exist') })
  110 |     if (method === 'PATCH') {
  111 |       const moved = 'parent_id' in body && body.parent_id !== target.parent_id
  112 |       const parent = this.rows.find(r => r.id === body.parent_id)
  113 |       const local = (body.code ?? target.code).split('.').at(-1)
  114 |       const code = moved ? (parent ? `${parent.code}.${local}` : local) : body.code ?? target.code
  115 |       const plan = new Map<number, string>()
  116 |       if (moved || code !== target.code) {
  117 |         const walk = (item: Row, next: string) => {
  118 |           plan.set(item.id, next)
  119 |           for (const child of this.rows.filter(r => r.parent_id === item.id)) walk(child, `${next}.${child.code.split('.').at(-1)}`)
  120 |         }
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
> 188 |   await dialog.locator('#category-form-parentId').selectOption('root')
      |                                                   ^ Error: locator.selectOption: Error: Element is not a <select> element
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
  221 |   await expect(rootDialog.locator('#category-form-parentId')).toHaveValue('root')
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
```