# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 后代编码超长时不能保存，也不会发送部分更新
- Location: e2e\category-config.spec.ts:423:1

# Error details

```
Error: locator.selectOption: Error: Element is not a <select> element
Call log:
  - waiting for getByRole('dialog', { name: '编辑类别' }).locator('#category-form-parentId')
    - locator resolved to <input value="" role="combobox" autocomplete="off" aria-haspopup="tree" aria-expanded="false" aria-controls="_r_5_" placeholder="搜索以更换上级类别" aria-autocomplete="list" id="category-form-parentId" aria-describedby="category-form-parentId-hint"/>
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
                    - generic [ref=e229]:
                      - button "收起「食材」" [expanded] [ref=e230] [cursor=pointer]
                      - button [ref=e236] [cursor=pointer]:
                        - generic [ref=e237]:
                          - strong [ref=e238]: 食材
                          - generic [aria-hidden] [ref=e239]: "1"
                        - code [ref=e240]: ingredient
                    - generic [ref=e241]: —
                    - generic [ref=e242]: —
                    - generic [ref=e243]: —
                    - generic [ref=e244]: —
                    - generic [ref=e245]: 启用
                    - generic [ref=e247]:
                      - button "新建「食材」的下级类别" [ref=e248] [cursor=pointer]
                      - button "编辑「食材」" [ref=e250] [cursor=pointer]
                      - button "删除「食材」" [ref=e254] [cursor=pointer]
                  - list [ref=e258]:
                    - listitem [ref=e259]:
                      - generic [ref=e260]:
                        - button [ref=e266] [cursor=pointer]:
                          - strong [ref=e268]: 长编码父类
                          - code [ref=e269]: ingredient.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
                        - generic [ref=e270]: —
                        - generic [ref=e271]: —
                        - generic [ref=e272]: —
                        - generic [ref=e273]: —
                        - generic [ref=e274]: 启用
                        - generic [ref=e276]:
                          - button "新建「长编码父类」的下级类别" [ref=e277] [cursor=pointer]
                          - button "编辑「长编码父类」" [ref=e279] [cursor=pointer]
                          - button "删除「长编码父类」" [ref=e283] [cursor=pointer]
            - generic [ref=e287]:
              - generic [ref=e288]: 共 6 个类别
              - generic [ref=e289]:
                - button "全部展开" [ref=e290] [cursor=pointer]
                - button "全部收起" [ref=e291] [cursor=pointer]
                - button "刷新" [ref=e292] [cursor=pointer]
          - paragraph [ref=e296]: 这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，标在它和下级类别上的图片会随即重算场景归属，并同步到向量库。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。
          - dialog [ref=e297]:
            - generic [ref=e298]:
              - heading "编辑类别" [level=2] [ref=e299]
              - button "关闭弹窗" [ref=e300] [cursor=pointer]
            - generic [ref=e305]:
              - generic [ref=e306]:
                - paragraph [ref=e307]: 1 个下级类别 · 0 张图片 · 0 条场景规则 · 更新于 2026-11-10 02:30
                - region [ref=e308]:
                  - heading "位置与编码" [level=3] [ref=e309]
                  - generic [ref=e310]:
                    - generic [ref=e311]: 上级类别
                    - generic [ref=e312]:
                      - generic [ref=e314]:
                        - strong [ref=e315]: 菜品
                        - code [ref=e316]: dish
                      - combobox "上级类别" [ref=e322]
                    - generic [ref=e323]: 换上级会移动整棵子树，并按新父级的完整编码更新当前节点及所有下级编码。
                  - generic [ref=e324]:
                    - generic [ref=e325]: 编码 *
                    - textbox "编码 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。" [ref=e326]: dish.pizza
                    - generic [ref=e327]: 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。
                - region [ref=e328]:
                  - heading "名称" [level=3] [ref=e329]
                  - generic [ref=e330]:
                    - generic [ref=e331]: 中文名称 *
                    - textbox "中文名称 检索结果和图库里显示的类别名。" [active] [ref=e332]: 披萨
                    - generic [ref=e333]: 检索结果和图库里显示的类别名。
                  - group "其他名称" [ref=e334]:
                    - button "添加名称" [ref=e337] [cursor=pointer]
                    - generic [ref=e339]: 中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名；搜索时别名也能找到它。
                - region [ref=e340]:
                  - heading "属性" [level=3] [ref=e341]
                  - generic [ref=e342]:
                    - generic [ref=e343]: 菜系
                    - textbox "菜系 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。" [ref=e344]:
                      - /placeholder: chinese, american
                      - text: italian
                    - generic [ref=e345]: 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。
                  - generic [ref=e346]:
                    - generic [ref=e347]: 加热组
                    - combobox "加热组 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。" [ref=e348]:
                      - option "不设置" [selected]
                      - option "烤（grill）"
                    - generic [ref=e349]: 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。
                  - generic [ref=e350]:
                    - generic [ref=e351]: 同级排序
                    - spinbutton "同级排序 整数，同一上级下数字小的排在前面。" [ref=e352]: "0"
                    - generic [ref=e353]: 整数，同一上级下数字小的排在前面。
                  - generic [ref=e354]:
                    - generic [ref=e355]: 状态
                    - generic [ref=e356] [cursor=pointer]:
                      - checkbox "启用" [checked] [ref=e357]
                      - text: 启用
                    - generic [ref=e358]: 停用后上传和图库编辑里不再列出它；它的图片和下级类别不受影响。
              - generic [ref=e359]:
                - button "取消" [ref=e360] [cursor=pointer]
                - button "保存" [ref=e361] [cursor=pointer]
  - status
```

# Test source

```ts
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
  386 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
  387 |   await dialog.locator('#category-form-parentId').selectOption('5')
  388 |   await expect(dialog.locator('#category-form-code')).toHaveValue('ingredient.PiZza')
  389 |   const preview = dialog.getByRole('status', { name: '编码更新预览' })
  390 |   await expect(preview).toContainText('一起更新 3 个节点')
  391 |   await expect(preview).toContainText('ingredient.PiZza.SliCe.TipPart')
  392 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  393 |   await expect(dialog).toBeHidden()
  394 |   expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/api/categories/2', body: { parent_id: 5, code: 'ingredient.PiZza' } }])
  395 |   expect(backend.rows.filter(item => [2, 4, 6].includes(item.id)).map(item => [item.id, item.code])).toEqual([
  396 |     [2, 'ingredient.PiZza'], [4, 'ingredient.PiZza.SliCe'], [6, 'ingredient.PiZza.SliCe.TipPart'],
  397 |   ])
  398 |   await page.getByRole('button', { name: '展开「披萨」' }).click()
  399 |   await page.getByRole('button', { name: '展开「披萨切片」' }).click()
  400 |   await expect(nodeButton(page, '切片尖角', 'ingredient.PiZza.SliCe.TipPart')).toBeVisible()
  401 | 
  402 |   await page.getByRole('button', { name: '编辑「披萨」' }).click()
  403 |   await dialog.locator('#category-form-code').fill('ingredient.PIZZA')
  404 |   await expect(preview).toContainText('ingredient.PIZZA.SliCe.TipPart')
  405 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  406 |   await expect(dialog).toBeHidden()
  407 |   expect(backend.writes()[1]).toEqual({ method: 'PATCH', path: '/api/categories/2', body: { code: 'ingredient.PIZZA' } })
  408 |   expect(backend.rows.find(item => item.id === 6)?.code).toBe('ingredient.PIZZA.SliCe.TipPart')
  409 | })
  410 | 
  411 | test('后代编码与外部类别冲突时，在发请求前阻止整棵子树的移动', async ({ page }) => {
  412 |   const backend = await open(page, [...seededRows(), row(9, 'Ingredient.PIZZA.SLICE', 5, '外部切片')])
  413 |   await page.getByRole('button', { name: '编辑「披萨」' }).click()
  414 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
  415 |   await dialog.locator('#category-form-parentId').selectOption('5')
  416 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  417 |   await expect(dialog.locator('#category-form-code-error')).toContainText('披萨切片')
  418 |   await expect(dialog.locator('#category-form-code-error')).toContainText('ingredient.pizza.slice')
  419 |   expect(backend.writes()).toHaveLength(0)
  420 |   expect(backend.rows.find(item => item.id === 2)?.parent_id).toBe(1)
  421 | })
  422 | 
  423 | test('后代编码超长时不能保存，也不会发送部分更新', async ({ page }) => {
  424 |   const parentCode = `ingredient.${'A'.repeat(106)}`
  425 |   const backend = await open(page, [...seededRows(), row(8, parentCode, 5, '长编码父类')])
  426 |   await page.getByRole('button', { name: '编辑「披萨」' }).click()
  427 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
> 428 |   await dialog.locator('#category-form-parentId').selectOption('8')
      |                                                   ^ Error: locator.selectOption: Error: Element is not a <select> element
  429 |   await expect(dialog.locator('#category-form-code')).toHaveValue(`${parentCode}.pizza`)
  430 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  431 |   await expect(dialog.locator('#category-form-code-error')).toContainText('披萨切片')
  432 |   await expect(dialog.locator('#category-form-code-error')).toContainText('128')
  433 |   expect(backend.writes()).toHaveLength(0)
  434 | })
  435 | 
  436 | test('大小写编码会保留；仅修改自身大小写可保存，其他类别不能重复使用', async ({ page }) => {
  437 |   const backend = await open(page, seededRows())
  438 |   await page.getByRole('button', { name: '新建「食材」的下级类别' }).click()
  439 |   const create = page.getByRole('dialog', { name: '新建类别' })
  440 |   await create.locator('#category-form-code').fill('Ingredient.HongSu')
  441 |   await create.locator('#category-form-zhName').fill('红薯')
  442 |   await create.getByRole('button', { name: '创建', exact: true }).click()
  443 |   await expect(create).toBeHidden()
  444 |   expect(backend.writes()[0].body).toMatchObject({ code: 'Ingredient.HongSu', parent_id: 5 })
  445 |   await expect(nodeButton(page, '红薯', 'Ingredient.HongSu')).toBeVisible()
  446 | 
  447 |   await page.getByRole('button', { name: '编辑「红薯」' }).click()
  448 |   const edit = page.getByRole('dialog', { name: '编辑类别' })
  449 |   await edit.locator('#category-form-code').fill('ingredient.hongsu')
  450 |   await edit.getByRole('button', { name: '保存', exact: true }).click()
  451 |   await expect(edit).toBeHidden()
  452 |   expect(backend.writes()[1]).toMatchObject({ method: 'PATCH', body: { code: 'ingredient.hongsu' } })
  453 | 
  454 |   await page.getByRole('button', { name: '新建「食材」的下级类别' }).click()
  455 |   await create.locator('#category-form-code').fill('INGREDIENT.HONGSU')
  456 |   await create.locator('#category-form-zhName').fill('重复红薯')
  457 |   await create.getByRole('button', { name: '创建', exact: true }).click()
  458 |   await expect(create.locator('#category-form-code-error')).toHaveText('该编码已被其他类别使用。')
  459 |   expect(backend.writes()).toHaveLength(2)
  460 | })
  461 | 
  462 | test('已有大小写类别域时不再提供相同根编码', async ({ page }) => {
  463 |   const backend = await open(page, [row(1, 'Ingredient', null, '食材')])
  464 |   await createButton(page).click()
  465 |   const dialog = page.getByRole('dialog', { name: '新建类别' })
  466 |   await dialog.locator('#category-form-parentId').selectOption('root')
  467 |   await expect(dialog.locator('#category-form-code option')).toHaveText([
  468 |     '菜品（dish）', '甜点烘焙（dessert）', '饮品（beverage）', '混合（mixed）', '其他（other）',
  469 |   ])
  470 |   expect(backend.writes()).toEqual([])
  471 | })
  472 | 
  473 | test('大小写类别域编码的子节点可以提升到根层级', async ({ page }) => {
  474 |   const backend = await open(page, [row(1, 'ingredient', null, '食材'), row(2, 'OtHeR', 1, '其他')])
  475 |   await page.getByRole('button', { name: '编辑「其他」' }).click()
  476 |   const dialog = page.getByRole('dialog', { name: '编辑类别' })
  477 |   await dialog.locator('#category-form-parentId').selectOption('root')
  478 |   await dialog.getByRole('button', { name: '保存', exact: true }).click()
  479 |   await expect(dialog).toBeHidden()
  480 |   expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/api/categories/2', body: { parent_id: null } }])
  481 |   await expect(nodeButton(page, '其他', 'OtHeR')).toBeVisible()
  482 | })
  483 | 
  484 | test('表单在发请求前校验，服务端拒绝时把原因显示在对应字段', async ({ page }) => {
  485 |   const backend = await open(page, seededRows())
  486 |   await page.getByRole('button', { name: '新建「菜品」的下级类别' }).click()
  487 |   const dialog = page.getByRole('dialog', { name: '新建类别' })
  488 |   const code = dialog.locator('#category-form-code')
  489 | 
  490 |   await code.fill('Dish Rice')
  491 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  492 |   await expect(dialog.locator('#category-form-code-error')).toContainText('小写字母')
  493 |   await expect(dialog.locator('#category-form-zhName-error')).toHaveText('请填写中文名称。')
  494 |   await expect(code).toBeFocused()
  495 |   await code.fill('dish.pizza')
  496 |   await expect(dialog.locator('#category-form-code-error')).toBeHidden() // editing a field clears its error
  497 |   await dialog.locator('#category-form-zhName').fill('米饭')
  498 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  499 |   await expect(dialog.locator('#category-form-code-error')).toHaveText('该编码已被其他类别使用。')
  500 |   expect(backend.writes()).toEqual([])
  501 | 
  502 |   // Somebody else created dish.rice after this page loaded its tree: the server refuses.
  503 |   backend.rows.push(row(9, 'dish.rice', 1, '米饭'))
  504 |   await code.fill('dish.rice')
  505 |   await dialog.getByRole('button', { name: '创建', exact: true }).click()
  506 |   await expect(dialog.locator('#category-form-code-error')).toHaveText('该编码已被其他类别使用。')
  507 |   await expect(dialog).toBeVisible()
  508 |   await expect(dialog.locator('#category-form-zhName')).toHaveValue('米饭') // what was typed is kept
  509 |   expect(backend.writes()).toHaveLength(1)
  510 | })
  511 | 
  512 | test('删除：在用的类别说明原因并可改为停用；空类别确认后删除', async ({ page }) => {
  513 |   const backend = await open(page, seededRows())
  514 |   const confirm = page.getByRole('dialog', { name: '删除类别' })
  515 | 
  516 |   await page.getByRole('button', { name: '删除「面条」' }).click()
  517 |   await expect(confirm).toContainText('还在使用中，不能删除')
  518 |   await expect(confirm).toContainText('3 张图片')
  519 |   await expect(confirm.getByRole('button', { name: '删除', exact: true })).toHaveCount(0)
  520 |   await confirm.getByRole('button', { name: '改为停用' }).click()
  521 |   await expect(confirm).toBeHidden()
  522 |   expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/api/categories/3', body: { is_active: false } }])
  523 |   await expectToast(page, '已停用「面条」。', 'success')
  524 |   await expect(rowOf(page, 3).locator('.ct-col-status')).toHaveText('停用')
  525 | 
  526 |   await page.getByRole('button', { name: '删除「披萨」' }).click()
  527 |   await expect(confirm).toContainText('1 个下级类别')
  528 |   await confirm.getByRole('button', { name: '关闭', exact: true }).click()
```