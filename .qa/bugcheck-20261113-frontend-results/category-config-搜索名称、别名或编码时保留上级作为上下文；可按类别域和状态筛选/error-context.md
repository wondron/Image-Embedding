# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: category-config.spec.ts >> 搜索名称、别名或编码时保留上级作为上下文；可按类别域和状态筛选
- Location: e2e\category-config.spec.ts:549:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.selectOption: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByLabel('类别域筛选')

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
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
                              - generic [aria-hidden] [ref=e177]: "2"
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
              - generic [ref=e256]: 共 6 个类别 · 停用 1 个
              - generic [ref=e257]:
                - button "全部展开" [ref=e258] [cursor=pointer]
                - button "全部收起" [ref=e259] [cursor=pointer]
                - button "刷新" [ref=e260] [cursor=pointer]
          - paragraph [ref=e264]: 这里的类别就是上传标注、图库筛选和场景规则里选用的食材类别。移动类别或修改菜系后，标在它和下级类别上的图片会随即重算场景归属，并同步到向量库。只有没有下级类别、图片和场景规则引用的类别才能删除，仍在使用的可以改为停用；加热组不在本页管理。
  - status
```

# Test source

```ts
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
> 566 |   await page.getByLabel('类别域筛选').selectOption({ label: '食材' })
      |                                  ^ Error: locator.selectOption: Test timeout of 30000ms exceeded.
  567 |   await expect(nodeButton(page, '食材', 'ingredient')).toBeVisible()
  568 |   await expect(nodeButton(page, '菜品', 'dish')).toHaveCount(0)
  569 |   await expect(footer).toContainText('共 1 个类别')
  570 | })
  571 | 
  572 | test('列表加载失败时显示错误码和追踪号，重试后恢复；链接可以直接打开某个类别', async ({ page }) => {
  573 |   const backend = new Backend(seededRows())
  574 |   // Not "the first request": in development StrictMode sends (and aborts) one request before the real one.
  575 |   backend.listFailure = { status: 503, json: errorBody('NOT_READY', 'retrieval stack is not initialized') }
  576 |   await page.route(url => url.pathname.startsWith('/api/'), route => {
  577 |     const path = new URL(route.request().url()).pathname
  578 |     if (path === '/api/ping') return route.fulfill({ json: { ok: true } })
  579 |     if (path === '/api/enums') return route.fulfill({ json: { ok: true, enums: [DOMAIN_ENUM] } })
  580 |     return backend.handle(route)
  581 |   })
  582 |   await page.goto('/?view=categories&category=4')
  583 |   const alert = page.getByRole('alert').filter({ hasText: '类别树加载失败' })
  584 |   await expect(alert).toContainText('NOT_READY')
  585 |   await expect(alert).toContainText('retrieval stack is not initialized')
  586 |   await expect(alert).toContainText('e2e-category')
  587 |   await expect(createButton(page)).toBeDisabled()
  588 | 
  589 |   backend.listFailure = null
  590 |   await alert.getByRole('button', { name: '重试' }).click()
  591 |   await expect(alert).toBeHidden()
  592 |   // ?category=4 highlights 披萨切片 and opens the path down to it.
  593 |   await expect(nodeButton(page, '披萨切片', 'dish.pizza.slice')).toHaveAttribute('aria-current', 'true')
  594 |   await expect(nodeButton(page, '披萨切片', 'dish.pizza.slice')).toBeInViewport()
  595 |   await expect(createButton(page)).toBeEnabled()
  596 | })
  597 | 
  598 | test('链接里的类别已不存在时说明原因，并从地址里去掉它', async ({ page }) => {
  599 |   await open(page, seededRows(), '/?view=categories&category=99')
  600 |   await acknowledgeNotice(page, '没有找到编号为 99 的类别，它可能已被删除。', 'warn')
  601 |   await expect(page).not.toHaveURL(/category=/)
  602 |   await expect(nodeButton(page, '菜品', 'dish')).toBeVisible()
  603 | })
  604 | 
  605 | test('窄屏：只留名称、停用标记和操作，没有横向滚动', async ({ page }) => {
  606 |   await page.setViewportSize({ width: 390, height: 844 })
  607 |   await open(page, seededRows().map(r => (r.id === 3 ? { ...r, is_active: false } : r)))
  608 |   await expect(nodeButton(page, '面条', 'dish.noodle')).toBeVisible()
  609 |   await expect(rowOf(page, 3).locator('.ct-label .ct-state')).toHaveText('停用') // the status column is hidden
  610 |   await expect(rowOf(page, 3).locator('.ct-col-status')).toBeHidden()
  611 |   await expect(page.getByRole('button', { name: '编辑「面条」' })).toBeVisible()
  612 |   expect(await noHorizontalOverflow(page)).toBe(true)
  613 |   await page.getByRole('button', { name: '编辑「面条」' }).click()
  614 |   await expect(page.getByRole('dialog', { name: '编辑类别' })).toBeVisible()
  615 |   expect(await noHorizontalOverflow(page)).toBe(true)
  616 | })
  617 | 
```