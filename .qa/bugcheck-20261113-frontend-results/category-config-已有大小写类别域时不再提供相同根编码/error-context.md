# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 已有大小写类别域时不再提供相同根编码
- Location: e2e\category-config.spec.ts:462:1

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
                      - code [ref=e142]: Ingredient
                    - generic [ref=e143]: —
                    - generic [ref=e144]: —
                    - generic [ref=e145]: —
                    - generic [ref=e146]: —
                    - generic [ref=e147]: 启用
                    - generic [ref=e149]:
                      - button "新建「食材」的下级类别" [ref=e150] [cursor=pointer]
                      - button "编辑「食材」" [ref=e152] [cursor=pointer]
                      - button "删除「食材」" [ref=e156] [cursor=pointer]
            - generic [ref=e160]:
              - generic [ref=e161]: 共 1 个类别
              - generic [ref=e162]:
                - button "全部展开" [ref=e163] [cursor=pointer]
                - button "全部收起" [ref=e164] [cursor=pointer]
                - button "刷新" [ref=e165] [cursor=pointer]
          - paragraph [ref=e169]: 这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，标在它和下级类别上的图片会随即重算场景归属，并同步到向量库。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。
          - dialog [ref=e170]:
            - generic [ref=e171]:
              - heading "新建类别" [level=2] [ref=e172]
              - button "关闭弹窗" [ref=e173] [cursor=pointer]
            - generic [ref=e178]:
              - generic [ref=e179]:
                - region [ref=e180]:
                  - heading "位置与编码" [level=3] [ref=e181]
                  - generic [ref=e182]:
                    - generic [ref=e183]: 上级类别 *
                    - generic [ref=e185]:
                      - combobox "上级类别" [expanded] [active] [ref=e190]
                      - tree "上级类别候选" [ref=e191]:
                        - treeitem "（根节点 · 类别域）" [level=1] [ref=e192] [cursor=pointer]
                        - treeitem "食材" [level=1] [ref=e195] [cursor=pointer]
                    - generic [ref=e198]: 新类别放在哪个类别下面；只有类别域（食材、菜品等）可以放在最顶层。
                  - generic [ref=e199]:
                    - generic [ref=e200]: 编码 *
                    - textbox "编码 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。" [disabled] [ref=e201]:
                      - /placeholder: 先选择上级类别
                    - generic [ref=e202]: 大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。
                - region [ref=e203]:
                  - heading "名称" [level=3] [ref=e204]
                  - generic [ref=e205]:
                    - generic [ref=e206]: 中文名称 *
                    - textbox "中文名称 检索结果和图库里显示的类别名。" [ref=e207]
                    - generic [ref=e208]: 检索结果和图库里显示的类别名。
                  - group "其他名称" [ref=e209]:
                    - button "添加名称" [ref=e212] [cursor=pointer]
                    - generic [ref=e214]: 中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名；搜索时别名也能找到它。
                - region [ref=e215]:
                  - heading "属性" [level=3] [ref=e216]
                  - generic [ref=e217]:
                    - generic [ref=e218]: 菜系
                    - textbox "菜系 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。" [ref=e219]:
                      - /placeholder: chinese, american
                    - generic [ref=e220]: 多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。
                  - generic [ref=e221]:
                    - generic [ref=e222]: 加热组
                    - combobox "加热组 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。" [ref=e223]:
                      - option "不设置" [selected]
                      - option "烤（grill）"
                    - generic [ref=e224]: 只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。
                  - generic [ref=e225]:
                    - generic [ref=e226]: 同级排序
                    - spinbutton "同级排序 整数，同一上级下数字小的排在前面。" [ref=e227]: "0"
                    - generic [ref=e228]: 整数，同一上级下数字小的排在前面。
                  - generic [ref=e229]:
                    - generic [ref=e230]: 状态
                    - generic [ref=e231] [cursor=pointer]:
                      - checkbox "启用" [checked] [ref=e232]
                      - text: 启用
                    - generic [ref=e233]: 停用后上传和图库编辑里不再列出它；它的图片和下级类别不受影响。
              - generic [ref=e234]:
                - button "取消" [ref=e235] [cursor=pointer]
                - button "创建" [ref=e236] [cursor=pointer]
  - status
```

# Test source

```ts
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
  428 |   await dialog.locator('#category-form-parentId').selectOption('8')
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
> 466 |   await dialog.locator('#category-form-parentId').selectOption('root')
      |                                                   ^ Error: locator.selectOption: Error: Element is not a <select> element
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
  529 | 
  530 |   await page.getByRole('button', { name: '展开「披萨」' }).click()
  531 |   await page.getByRole('button', { name: '删除「披萨切片」' }).click()
  532 |   await expect(confirm).toContainText('确定删除「披萨切片」（dish.pizza.slice）吗？')
  533 |   await confirm.getByRole('button', { name: '取消' }).click()
  534 |   await expect(confirm).toBeHidden()
  535 |   expect(backend.writes()).toHaveLength(1)
  536 | 
  537 |   await page.getByRole('button', { name: '删除「披萨切片」' }).click()
  538 |   await confirm.getByRole('button', { name: '删除', exact: true }).click()
  539 |   await expect(confirm).toBeHidden()
  540 |   expect(backend.writes()[1]).toEqual({ method: 'DELETE', path: '/api/categories/4', body: undefined })
  541 |   await expectToast(page, '已删除「披萨切片」。', 'success')
  542 |   await expect(nodeButton(page, '披萨切片', 'dish.pizza.slice')).toHaveCount(0)
  543 |   // The parent is highlighted next, and without children it can now be deleted too.
  544 |   await expect(nodeButton(page, '披萨', 'dish.pizza')).toHaveAttribute('aria-current', 'true')
  545 |   await page.getByRole('button', { name: '删除「披萨」' }).click()
  546 |   await expect(confirm).toContainText('确定删除「披萨」（dish.pizza）吗？')
  547 | })
  548 | 
  549 | test('搜索名称、别名或编码时保留上级作为上下文；可按类别域和状态筛选', async ({ page }) => {
  550 |   await open(page, [...seededRows(), row(6, 'dish.pizza.calzone', 2, '卡尔佐内', { names: [zh('卡尔佐内'), { lang: 'zh', name: '意式馅饼', kind: 'alias' }], is_active: false })])
  551 |   const footer = table(page).locator('.ct-footer')
  552 |   await page.getByLabel('搜索类别').fill('馅饼') // an alias
  553 |   await expect(table(page).getByRole('button', { name: /^(卡尔佐内|披萨|菜品)/ })).toHaveCount(3)
  554 |   await expect(nodeButton(page, '面条', 'dish.noodle')).toHaveCount(0)
  555 |   await expect(footer).toContainText('匹配 1 个 · 共 6 个类别')
  556 | 
  557 |   await page.getByLabel('搜索类别').fill('')
  558 |   await page.getByLabel('状态筛选').selectOption('inactive')
  559 |   await expect(nodeButton(page, '卡尔佐内', 'dish.pizza.calzone')).toBeVisible()
  560 |   await page.getByLabel('搜索类别').fill('面条')
  561 |   await expect(table(page).getByRole('heading', { name: '没有匹配的类别' })).toBeVisible()
  562 |   await table(page).getByRole('button', { name: '清空筛选' }).click()
  563 |   await expect(page.getByLabel('搜索类别')).toHaveValue('')
  564 |   await expect(nodeButton(page, '面条', 'dish.noodle')).toBeVisible()
  565 | 
  566 |   await page.getByLabel('类别域筛选').selectOption({ label: '食材' })
```