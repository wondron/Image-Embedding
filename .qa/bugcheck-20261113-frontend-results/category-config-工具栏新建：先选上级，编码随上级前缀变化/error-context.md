# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 工具栏新建：先选上级，编码随上级前缀变化
- Location: e2e\category-config.spec.ts:274:1

# Error details

```
Error: locator.selectOption: Error: Element is not a <select> element
Call log:
  - waiting for getByRole('dialog', { name: '新建类别' }).locator('#category-form-parentId')
    - locator resolved to <input value="" role="combobox" autocomplete="off" aria-invalid="true" aria-expanded="true" aria-haspopup="tree" aria-controls="_r_5_" aria-autocomplete="list" id="category-form-parentId" data-dialog-initial-focus="true" placeholder="搜索名称、编码或别名，或在下面逐级展开" aria-activedescendant="_r_5_-none" aria-describedby="category-form-parentId-error"/>
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
              - option "菜品"
              - option "披萨"
              - option "食材"
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
                    - generic [ref=e134]:
                      - button "收起「菜品」" [expanded] [ref=e135] [cursor=pointer]
                      - button [ref=e141] [cursor=pointer]:
                        - generic [ref=e142]:
                          - strong [ref=e143]: 菜品
                          - generic [aria-hidden] [ref=e144]: "2"
                        - code [ref=e145]: dish
                    - generic [ref=e146]: —
                    - generic [ref=e147]: —
                    - generic [ref=e148]: —
                    - generic "下级 3 张（下级类别上的图片合计，不分场景）" [ref=e150]: 下级 3 张
                    - generic [ref=e151]: 启用
                    - generic [ref=e153]:
                      - button "新建「菜品」的下级类别" [ref=e154] [cursor=pointer]
                      - button "编辑「菜品」" [ref=e156] [cursor=pointer]
                      - button "删除「菜品」" [ref=e160] [cursor=pointer]
                  - list [ref=e164]:
                    - listitem [ref=e165]:
                      - generic [ref=e166]:
                        - generic [ref=e167]:
                          - button "展开「披萨」" [ref=e168] [cursor=pointer]
                          - button [ref=e174] [cursor=pointer]:
                            - generic [ref=e175]:
                              - strong [ref=e176]: 披萨
                              - generic [aria-hidden] [ref=e177]: "1"
                            - code [ref=e178]: dish.pizza
                        - generic [ref=e179]: —
                        - generic [ref=e180]: italian
                        - generic [ref=e183]: —
                        - generic [ref=e184]: —
                        - generic [ref=e185]: 启用
                        - generic [ref=e187]:
                          - button "新建「披萨」的下级类别" [ref=e188] [cursor=pointer]
                          - button "编辑「披萨」" [ref=e190] [cursor=pointer]
                          - button "删除「披萨」" [ref=e194] [cursor=pointer]
                    - listitem [ref=e198]:
                      - generic [ref=e199]:
                        - button [ref=e205] [cursor=pointer]:
                          - strong [ref=e207]: 面条
                          - code [ref=e208]: dish.noodle
                        - generic [ref=e209]: —
                        - generic [ref=e210]: —
                        - generic [ref=e211]: —
                        - generic "3 张图片" [ref=e213]
                        - generic [ref=e214]: 启用
                        - generic [ref=e216]:
                          - button "新建「面条」的下级类别" [ref=e217] [cursor=pointer]
                          - button "编辑「面条」" [ref=e219] [cursor=pointer]
                          - button "删除「面条」" [ref=e223] [cursor=pointer]
                - listitem [ref=e227]:
                  - generic [ref=e228]:
                    - button [ref=e234] [cursor=pointer]:
                      - strong [ref=e236]: 食材
                      - code [ref=e237]: ingredient
                    - generic [ref=e238]: —
                    - generic [ref=e239]: —
                    - generic [ref=e240]: —
                    - generic [ref=e241]: —
                    - generic [ref=e242]: 启用
                    - generic [ref=e244]:
                      - button "新建「食材」的下级类别" [ref=e245] [cursor=pointer]
                      - button "编辑「食材」" [ref=e247] [cursor=pointer]
                      - button "删除「食材」" [ref=e251] [cursor=pointer]
            - generic [ref=e255]:
              - generic [ref=e256]: 共 5 个类别
              - generic [ref=e257]:
                - button "全部展开" [ref=e258] [cursor=pointer]
                - button "全部收起" [ref=e259] [cursor=pointer]
                - button "刷新" [ref=e260] [cursor=pointer]
          - paragraph [ref=e264]: 这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，标在它和下级类别上的图片会随即重算场景归属，并同步到向量库。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。
          - dialog [ref=e265]:
            - generic [ref=e266]:
              - heading "新建类别" [level=2] [ref=e267]
              - button "关闭弹窗" [ref=e268] [cursor=pointer]
            - generic [ref=e273]:
              - generic [ref=e274]:
                - region [ref=e275]:
                  - heading "位置与编码" [level=3] [ref=e276]
                  - generic [ref=e277]:
                    - generic [ref=e278]: 上级类别 *
                    - generic [ref=e280]:
                      - combobox "上级类别" [expanded] [active] [invalid] [ref=e285]
                      - tree "上级类别候选" [ref=e286]:
                        - treeitem "（根节点 · 类别域）" [level=1] [ref=e287] [cursor=pointer]
                        - treeitem "菜品" [expanded] [level=1] [ref=e290] [cursor=pointer]:
                          - generic [aria-hidden] [ref=e295]: "3"
                        - treeitem "披萨" [level=2] [ref=e296] [cursor=pointer]:
                          - generic [aria-hidden] [ref=e301]: "1"
                        - treeitem "面条" [level=2] [ref=e302] [cursor=pointer]
                        - treeitem "食材" [level=1] [ref=e305] [cursor=pointer]
                    - generic [ref=e308]: 新类别放在哪个类别下面；只有类别域（食材、菜品等）可以放在最顶层。
                    - alert [ref=e309]: 请选择上级类别。
                  - generic [ref=e310]:
                    - generic [ref=e311]: 编码 *
                    - textbox "编码 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。" [disabled] [ref=e312]:
                      - /placeholder: 先选择上级类别
                    - generic [ref=e313]: 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。
                - region [ref=e314]:
                  - heading "名称" [level=3] [ref=e315]
                  - generic [ref=e316]:
                    - generic [ref=e317]: 中文名称 *
                    - textbox "中文名称 检索结果和图库里显示的类别名。" [ref=e318]
                    - generic [ref=e319]: 检索结果和图库里显示的类别名。
                  - group "其他名称" [ref=e320]:
                    - button "添加名称" [ref=e323] [cursor=pointer]
                    - generic [ref=e325]: 中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名；搜索时别名也能找到它。
                - region [ref=e326]:
                  - heading "属性" [level=3] [ref=e327]
                  - generic [ref=e328]:
                    - generic [ref=e329]: 菜系
                    - textbox "菜系 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。" [ref=e330]:
                      - /placeholder: chinese, american
                    - generic [ref=e331]: 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。
                  - generic [ref=e332]:
                    - generic [ref=e333]: 加热组
                    - combobox "加热组 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。" [ref=e334]:
                      - option "不设置" [selected]
                      - option "烤（grill）"
                    - generic [ref=e335]: 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。
                  - generic [ref=e336]:
                    - generic [ref=e337]: 同级排序
                    - spinbutton "同级排序 整数，同一上级下数字小的排在前面。" [ref=e338]: "0"
                    - generic [ref=e339]: 整数，同一上级下数字小的排在前面。
                  - generic [ref=e340]:
                    - generic [ref=e341]: 状态
                    - generic [ref=e342] [cursor=pointer]:
                      - checkbox "启用" [checked] [ref=e343]
                      - text: 启用
                    - generic [ref=e344]: 停用后上传和图库编辑里不再列出它；它的图片和下级类别不受影响。
              - generic [ref=e345]:
                - button "取消" [ref=e346] [cursor=pointer]
                - button "创建" [ref=e347] [cursor=pointer]
  - status
```

# Test source

```ts
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
> 285 |   await parent.selectOption('2') // 披萨
      |                ^ Error: locator.selectOption: Error: Element is not a <select> element
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
  322 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
  323 |   await expect(dialog.locator('#category-form-zhName')).toBeFocused()
  324 |   await expect(dialog).toContainText('1 个下级类别 · 0 张图片 · 0 条场景规则')
  325 |   // A node cannot move under itself or its own subtree: those are not offered.
  326 |   await expect(dialog.locator('#category-form-parentId option')).toHaveText(['菜品（dish）', '　面条（dish.noodle）', '食材（ingredient）'])
  327 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  328 |   await expectToast(page, '没有改动，无需保存。', 'info')
  329 |   expect(backend.writes()).toEqual([])
  330 | 
  331 |   await dialog.locator('#category-form-sortOrder').fill('5')
  332 |   await dialog.getByRole('checkbox', { name: '启用' }).uncheck()
  333 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  334 |   await expect(dialog).toBeHidden()
  335 |   expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/api/categories/2', body: { sort_order: 5, is_active: false } }])
  336 |   await expect(rowOf(page, 2).locator('.ct-col-status')).toHaveText('停用')
  337 | 
  338 |   await nodeButton(page, '披萨', 'dish.pizza').click() // the name opens the editor too
  339 |   await dialog.locator('#category-form-parentId').selectOption('5')
  340 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  341 |   await expect(dialog).toBeHidden()
  342 |   expect(backend.writes()[1]).toEqual({ method: 'PATCH', path: '/api/categories/2', body: { parent_id: 5, code: 'ingredient.pizza' } })
  343 |   // 披萨 now sits under 食材 (opened to show it) and brought 披萨切片 with it.
  344 |   // (a `has` locator is resolved inside the list item, so it must not start from the region)
  345 |   const ingredient = table(page).getByRole('listitem').filter({ has: page.getByRole('button', { name: /^食材\s*ingredient$/ }) })
  346 |   await expect(ingredient.getByRole('button', { name: /^披萨\s*(停用\s*)?ingredient\.pizza$/ })).toBeVisible()
  347 |   await page.getByRole('button', { name: '展开「披萨」' }).click()
  348 |   await expect(ingredient.getByRole('button', { name: /^披萨切片\s*ingredient\.pizza\.slice$/ })).toBeVisible()
  349 | })
  350 | 
  351 | test('移动类别或改菜系后，说明其下图片的场景归属；没能同步到向量库的说明原因', async ({ page }) => {
  352 |   const backend = await open(page, seededRows())
  353 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
  354 | 
  355 |   backend.sceneSync = { images: 3, changed: 2, pushed: 2, pending: 0, vector_sync: 'pushed', sync_required: false }
  356 |   await page.getByRole('button', { name: '编辑「面条」' }).click()
  357 |   await dialog.locator('#category-form-parentId').selectOption('5')
  358 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  359 |   await expect(dialog).toBeHidden()
  360 |   await expectToast(page, '已保存「面条」。其下 2 张图片的场景归属已随之更新，并已同步到向量库。', 'success')
  361 | 
  362 |   // Saved, but the vector store is behind: that is worth reading before going on
  363 |   backend.sceneSync = { images: 3, changed: 3, pushed: 1, pending: 2, vector_sync: 'not_ready', sync_required: true }
  364 |   await page.getByRole('button', { name: '编辑「面条」' }).click()
  365 |   await dialog.locator('#category-form-cuisines').fill('chinese')
  366 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  367 |   await expect(dialog).toBeHidden()
  368 |   await acknowledgeNotice(page, '其下 3 张图片的场景归属已随之更新，其中 2 张还没同步到向量库：检索服务未就绪，恢复后后台会自动补上。', 'warn')
  369 |   expect(backend.writes().map(call => call.body)).toEqual([{ parent_id: 5, code: 'ingredient.noodle' }, { cuisines: ['chinese'] }])
  370 | 
  371 |   // Nothing below changed scenes: the plain save
  372 |   backend.sceneSync = { images: 3, changed: 0, pushed: 0, pending: 0, vector_sync: 'pushed', sync_required: false }
  373 |   await page.getByRole('button', { name: '编辑「面条」' }).click()
  374 |   await dialog.locator('#category-form-parentId').selectOption('1')
  375 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  376 |   await expectToast(page, /^已保存「面条」。$/, 'success')
  377 | })
  378 | 
  379 | test('换父级或修改节点编码时，预览并以一个请求更新所有后代编码', async ({ page }) => {
  380 |   const rows = seededRows()
  381 |   rows.find(item => item.id === 2)!.code = 'dish.PiZza'
  382 |   rows.find(item => item.id === 4)!.code = 'legacy.SliCe'
  383 |   rows.push(row(6, 'legacy.TipPart', 4, '切片尖角'))
  384 |   const backend = await open(page, rows)
  385 |   await page.getByRole('button', { name: '编辑「披萨」' }).click()
```