# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: image-data-detail-v03.spec.ts >> image-data-detail v0.3 isolated acceptance >> P0: 类别与标注状态横排/裁切/对齐与选中值
- Location: image-data-detail-v03.spec.ts:435:7

# Error details

```
TimeoutError: locator.fill: Timeout 12000ms exceeded.
Call log:
  - waiting for getByRole('dialog', { name: '图片标注' }).getByRole('combobox', { name: '类别' })
    - locator resolved to <input value="" role="combobox" autocomplete="off" id="_r_7_-category" aria-expanded="false" aria-controls="_r_9_" placeholder="搜索以更换类别" aria-autocomplete="list"/>
    - fill("红烧")
  - attempting fill action
    2 × waiting for element to be visible, enabled and editable
      - element is not visible
    - retrying fill action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and editable
      - element is not visible
    - retrying fill action
      - waiting 100ms
    23 × waiting for element to be visible, enabled and editable
       - element is not visible
     - retrying fill action
       - waiting 500ms

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
          - heading "图片数据" [level=1] [ref=e96]
          - generic [ref=e97]: 食材图像管理 / 管理你的食材图库
        - generic [ref=e98]:
          - button "使用说明" [ref=e99] [cursor=pointer]
          - button "连接设置" [ref=e103] [cursor=pointer]
      - main [ref=e107]:
        - generic [ref=e109]:
          - complementary "图片筛选" [ref=e110]:
            - generic [ref=e111]:
              - heading "筛选" [level=2] [ref=e113]
              - button "筛选说明" [ref=e114] [cursor=pointer]
              - button "重置" [disabled] [ref=e118]
            - generic [ref=e119]:
              - generic [ref=e120]:
                - searchbox "关键词" [ref=e125]
                - generic [ref=e126]:
                  - generic [ref=e127]: 场景
                  - combobox "场景" [ref=e129]:
                    - option "通用（general）" [selected]
                    - option "一体机（oven）"
                    - option "非常长的场景名称用来测试左栏换行而不撑破对话框（long-scene）"
                  - button "刷新场景" [ref=e130] [cursor=pointer]
                - generic [ref=e134]:
                  - generic [aria-hidden] [ref=e135]: 标注
                  - radiogroup "标注状态" [ref=e136]:
                    - generic [ref=e137] [cursor=pointer]:
                      - radio "全部" [checked] [ref=e138]
                      - generic [ref=e139]: 全部
                    - generic "还没有类别的图片" [ref=e140] [cursor=pointer]:
                      - radio "未标注" [ref=e141]
                      - generic [ref=e142]: 未标注
                    - generic "有类别，还没有人工核验" [ref=e143] [cursor=pointer]:
                      - radio "自动标注" [ref=e144]
                      - generic [ref=e145]: 自动标注
                    - generic "类别经过人工核验" [ref=e146] [cursor=pointer]:
                      - radio "已核验" [ref=e147]
                      - generic [ref=e148]: 已核验
                - generic [ref=e149]:
                  - generic [aria-hidden] [ref=e150]: 向量
                  - radiogroup "向量库状态" [ref=e151]:
                    - generic [ref=e152] [cursor=pointer]:
                      - radio "全部" [checked] [ref=e153]
                      - generic [ref=e154]: 全部
                    - generic "按写入记录：向量库里的过滤信息落后于数据库，或当前模型还没有这张图的向量" [ref=e155] [cursor=pointer]:
                      - radio "待同步或没有向量" [ref=e156]
                      - generic [ref=e157]: 待同步或没有向量
                - generic [ref=e158]:
                  - generic [ref=e159]: 标签
                  - generic "和标签完全一致才算；也可以点卡片上的标签" [ref=e160]:
                    - textbox "标签" [ref=e164]:
                      - /placeholder: 完整标签，回车筛选
              - region [ref=e165]:
                - generic [ref=e166]:
                  - heading "类别" [level=3] [ref=e167]
                  - generic [ref=e168]: 5 个启用
                  - button "重新读取类别和图片数量" [ref=e169] [cursor=pointer]
                - searchbox "搜索类别" [ref=e177]
                - generic [ref=e178]:
                  - generic [ref=e179] [cursor=pointer]:
                    - checkbox "隐藏没有图片的" [ref=e180]
                    - text: 隐藏没有图片的
                  - generic [ref=e181] [cursor=pointer]:
                    - checkbox "显示停用的" [ref=e182]
                    - text: 显示停用的
                - tree "类别" [ref=e183]:
                  - treeitem "菜品，0 张" [level=1] [ref=e184] [cursor=pointer]:
                    - generic [ref=e188]: 菜品
                    - generic [ref=e189]: "0"
                - paragraph [ref=e190]: 点选即筛选（含下级）· 数字为图片数，不分场景
          - region [ref=e191]:
            - generic [ref=e192]:
              - generic [ref=e193]:
                - heading "图片" [level=2] [ref=e194]
                - generic [ref=e195]: 5 张
                - generic [ref=e196]: 图库已接入
              - generic [ref=e198]:
                - generic [ref=e199]:
                  - generic [ref=e200]: 每页
                  - combobox "每页数量" [ref=e201]:
                    - option "24 张" [selected]
                    - option "48 张"
                    - option "96 张"
                - group "图片展示方式" [ref=e202]:
                  - button "大图展示" [pressed] [ref=e203] [cursor=pointer]
                  - button "小图展示" [ref=e206] [cursor=pointer]
                  - button "列表展示" [ref=e209] [cursor=pointer]
                - button "导入图片" [ref=e211] [cursor=pointer]
            - toolbar "批量操作" [ref=e213]:
              - generic [ref=e214] [cursor=pointer]:
                - checkbox "选择本页" [ref=e215]
                - text: 本页
              - status [ref=e216]: 可勾选图片后批量操作
              - generic [ref=e217]:
                - button "批量修改" [disabled] [ref=e218]
                - button "批量删除" [disabled] [ref=e222]
            - generic [ref=e227]:
              - article [ref=e228]:
                - generic "选择（按住 Shift 可连选）" [ref=e229] [cursor=pointer]:
                  - checkbox "选择 煎牛排" [ref=e230]
                - button "标注 煎牛排" [ref=e231] [cursor=pointer]:
                  - generic [ref=e232]: 查看与标注
                - generic [ref=e233]:
                  - strong [ref=e234]: 煎牛排
                  - generic [ref=e235]:
                    - button "按类别筛选：牛排" [ref=e236] [cursor=pointer]: 牛排
                    - generic [ref=e237]: 自动标注
                    - generic "向量库里的过滤信息落后于数据库，检索可能按旧标注命中它；打开图片可以同步" [ref=e238]: 待同步
                    - generic [ref=e239]: 通用
                    - generic [ref=e240]: 一体机
                  - button "按标签筛选：带骨" [ref=e242] [cursor=pointer]: "#带骨"
              - article [ref=e243]:
                - generic "选择（按住 Shift 可连选）" [ref=e244] [cursor=pointer]:
                  - 'checkbox "选择 #00000000" [ref=e245]'
                - 'button "标注 #00000000" [ref=e246] [cursor=pointer]':
                  - generic [ref=e247]: 查看与标注
                - generic [ref=e248]:
                  - strong [ref=e249]: "#00000000"
                  - generic [ref=e250]:
                    - generic [ref=e251]: 未标注
                    - generic [ref=e252]: 通用
              - article [ref=e253]:
                - generic "选择（按住 Shift 可连选）" [ref=e254] [cursor=pointer]:
                  - checkbox "选择 长路径图" [ref=e255]
                - button "标注 长路径图" [ref=e256] [cursor=pointer]:
                  - generic [ref=e257]: 查看与标注
                - generic [ref=e258]:
                  - strong [ref=e259]: 长路径图
                  - generic [ref=e260]:
                    - button "按类别筛选：超长路径名称用于观察横排是否裁切或换行的类别" [ref=e261] [cursor=pointer]: 超长路径名称用于观察横排是否裁切或换行的类别
                    - generic [ref=e262]: 自动标注
                    - generic [ref=e263]: 通用
                    - generic [ref=e264]: 一体机
                    - generic [ref=e265]: 非常长的场景名称用来测试左栏换行而不撑破对话框
                  - button "按标签筛选：超长标签名称用于观察标签控件是否对齐或裁切ABCDEF" [ref=e267] [cursor=pointer]: "#超长标签名称用于观察标签控件是否对齐或裁切ABCDEF"
              - article [ref=e268]:
                - generic "选择（按住 Shift 可连选）" [ref=e269] [cursor=pointer]:
                  - checkbox "选择 仅原图失败" [ref=e270]
                - button "标注 仅原图失败" [ref=e271] [cursor=pointer]:
                  - generic [ref=e272]: 查看与标注
                - generic [ref=e273]:
                  - strong [ref=e274]: 仅原图失败
                  - generic [ref=e275]:
                    - button "按类别筛选：牛排" [ref=e276] [cursor=pointer]: 牛排
                    - generic [ref=e277]: 自动标注
                    - generic [ref=e278]: 通用
                    - generic [ref=e279]: 一体机
                  - button "按标签筛选：带骨" [ref=e281] [cursor=pointer]: "#带骨"
              - article [ref=e282]:
                - generic "选择（按住 Shift 可连选）" [ref=e283] [cursor=pointer]:
                  - checkbox "选择 双源失败" [ref=e284]
                - button "标注 双源失败" [ref=e285] [cursor=pointer]:
                  - generic [ref=e292]: 查看与标注
                - generic [ref=e293]:
                  - strong [ref=e294]: 双源失败
                  - generic [ref=e295]:
                    - button "按类别筛选：牛排" [ref=e296] [cursor=pointer]: 牛排
                    - generic [ref=e297]: 自动标注
                    - generic "向量库里没有这张图，检索查不到它；打开图片可以用原图重新生成" [ref=e298]: 没有向量
                    - generic [ref=e299]: 通用
                    - generic [ref=e300]: 一体机
                  - button "按标签筛选：带骨" [ref=e302] [cursor=pointer]: "#带骨"
            - generic [ref=e303]:
              - generic [ref=e304]:
                - text: 共 5 张 · 第 1–5 张
                - generic [ref=e305]: · 本页 1 张待同步、1 张没有向量
              - navigation "翻页" [ref=e306]:
                - button "第一页" [disabled] [ref=e307]
                - button "上一页" [disabled] [ref=e311]
                - generic [ref=e314]:
                  - generic [ref=e315]: 第
                  - spinbutton "页码" [ref=e316]: "1"
                  - generic [ref=e317]: / 1 页
                - button "下一页" [disabled] [ref=e318]
                - button "最后一页" [disabled] [ref=e321]
          - dialog [ref=e325]:
            - generic [ref=e326]:
              - heading "图片标注" [level=2] [ref=e327]
              - button "关闭弹窗" [active] [ref=e328] [cursor=pointer]
            - generic [ref=e333]:
              - generic [ref=e334]:
                - complementary "已保存信息" [ref=e335]:
                  - figure [ref=e336]:
                    - img "煎牛排" [ref=e337]
                  - generic [ref=e338]:
                    - heading "已保存信息" [level=3] [ref=e339]
                    - region "分类归属" [ref=e340]:
                      - heading "分类归属" [level=4] [ref=e341]
                      - generic [ref=e342]:
                        - generic [ref=e343]:
                          - term [ref=e344]: 类别
                          - definition [ref=e345]: 菜品 / 肉类 / 牛排
                        - generic [ref=e346]:
                          - term [ref=e347]: 标注状态
                          - definition [ref=e348]: 自动标注
                        - generic [ref=e349]:
                          - term [ref=e350]: 所属场景
                          - definition [ref=e351]: 通用、一体机
                    - region "规格与来源" [ref=e352]:
                      - heading "规格与来源" [level=4] [ref=e353]
                      - generic [ref=e354]:
                        - generic [ref=e355]:
                          - term [ref=e356]: 尺寸
                          - definition [ref=e357]: 640 × 480
                        - generic [ref=e358]:
                          - term [ref=e359]: 来源 · 许可
                          - definition [ref=e360]: 用户上传 · 未知
                        - generic [ref=e361]:
                          - term [ref=e362]: 入库时间
                          - definition [ref=e363]: 2026-11-10 01:00
                    - region "标识" [ref=e364]:
                      - heading "标识" [level=4] [ref=e365]
                      - generic [ref=e367]:
                        - term [ref=e368]: 图片 ID
                        - definition [ref=e369]: 00000000-0000-4000-8000-00000000000a
                - generic [ref=e370]:
                  - region "向量库状态" [ref=e371]:
                    - generic [ref=e372]:
                      - strong [ref=e373]: 向量库
                      - generic [ref=e374]: 待同步
                    - paragraph [ref=e375]: 向量库里还是旧的过滤信息，检索可能按旧的类别或场景命中这张图。
                    - table [ref=e376]:
                      - rowgroup [ref=e377]:
                        - row [ref=e378]:
                          - columnheader "不一致" [ref=e379]
                          - columnheader "向量库里" [ref=e380]
                          - columnheader "数据库里" [ref=e381]
                      - rowgroup [ref=e382]:
                        - row [ref=e383]:
                          - rowheader "所属场景" [ref=e384]
                          - cell "通用" [ref=e385]
                          - cell "通用、一体机" [ref=e386]
                    - button "按数据库同步到向量库" [ref=e387] [cursor=pointer]
                    - group [ref=e391]:
                      - generic "技术信息" [ref=e392] [cursor=pointer]
                  - form "编辑标注" [ref=e393]:
                    - region [ref=e394]:
                      - heading "类别与标注状态" [level=3] [ref=e395]
                      - generic [ref=e396]:
                        - generic [ref=e397]:
                          - generic [ref=e398]: 类别
                          - generic [ref=e399]:
                            - generic:
                              - strong: 牛排
                              - generic: 菜品 / 肉类 / 牛排
                              - code: dish.meat.steak
                            - button "清除类别 牛排" [ref=e400] [cursor=pointer]
                          - generic [ref=e405]:
                            - combobox "类别"
                        - group "标注状态" [ref=e408]:
                          - generic [ref=e410] [cursor=pointer]:
                            - radio "自动标注" [checked] [ref=e411]
                            - text: 自动标注
                          - generic [ref=e412] [cursor=pointer]:
                            - radio "已核验" [ref=e413]
                            - text: 已核验
                          - generic [ref=e414]: 换了类别默认记为「已核验」：是人在图库里选的。
                    - region [ref=e415]:
                      - heading "标签与描述" [level=3] [ref=e416]
                      - generic [ref=e417]:
                        - generic [ref=e418]:
                          - generic [ref=e419]: 标签
                          - generic [ref=e420]:
                            - generic [ref=e421]:
                              - text: 带骨
                              - button "移除标签 带骨" [ref=e422] [cursor=pointer]
                            - textbox "标签" [ref=e426]:
                              - /placeholder: ""
                        - generic [ref=e427]:
                          - generic [ref=e428]: 描述
                          - textbox "描述" [ref=e429]: 煎牛排
                          - generic [ref=e430]: 3 / 1000
                    - group [ref=e431]:
                      - generic "食物标注 已填 1 项 · 生" [ref=e432] [cursor=pointer]:
                        - heading "食物标注" [level=3] [ref=e433]
                        - generic [ref=e434]: 已填 1 项 · 生
                      - generic [ref=e435]:
                        - generic [ref=e436]:
                          - generic [ref=e437]: 食物状态
                          - combobox "食物状态" [ref=e438]:
                            - option "未填写"
                            - option "生" [selected]
                            - option "熟"
                        - generic [ref=e439]:
                          - generic [ref=e440]: 加工状态
                          - combobox "加工状态" [ref=e441]:
                            - option "未填写" [selected]
                            - option "切片"
                        - generic [ref=e442]:
                          - generic [ref=e443]: 个数
                          - textbox "个数" [ref=e445]:
                            - /placeholder: ≥ 1
                        - generic [ref=e446]:
                          - generic [ref=e447]: 总质量
                          - generic [ref=e448]:
                            - textbox "总质量" [ref=e449]:
                              - /placeholder: ≥ 1
                            - generic [aria-hidden] [ref=e450]: g
                        - generic [ref=e451]:
                          - generic [ref=e452]: 含水质量
                          - generic [ref=e453]:
                            - textbox "含水质量" [ref=e454]:
                              - /placeholder: ≥ 0
                            - generic [aria-hidden] [ref=e455]: g
                        - generic [ref=e456]:
                          - generic [ref=e457]: 主食材质量
                          - generic [ref=e458]:
                            - textbox "主食材质量" [ref=e459]:
                              - /placeholder: ≥ 1
                            - generic [aria-hidden] [ref=e460]: g
                      - paragraph [ref=e461]: 食物状态参与检索过滤，改了会同步到向量库；其余字段只存在数据库里。
                  - generic [ref=e463]:
                    - generic [ref=e464]:
                      - heading "场景成员" [level=4] [ref=e465]
                      - button "刷新" [ref=e466] [cursor=pointer]
                    - alert [ref=e467]: 读取场景成员失败，暂不可修改。场景接口响应不符合契约，请刷新对账后再操作。
              - generic [ref=e469]:
                - button "删除图片" [ref=e470] [cursor=pointer]
                - button "撤销修改" [disabled] [ref=e474]
                - button "保存修改" [disabled] [ref=e475]
  - status
```

# Test source

```ts
  392 |       savedRail: railFacts,
  393 |       headingCategory: document.querySelector('#\\:r0\\:-category-title, .ie-form .ia-section h3')?.textContent || document.querySelector('.ie-form .ia-section h3')?.textContent || null,
  394 |       changesTitle: (document.querySelector('.ie-changes') as HTMLElement | null)?.getAttribute('title'),
  395 |       changesText: (document.querySelector('.ie-changes') as HTMLElement | null)?.textContent,
  396 |       foodOpen: (document.querySelector('details.ia-collapsible') as HTMLDetailsElement | null)?.open ?? null,
  397 |     }
  398 |   })
  399 | }
  400 | 
  401 | function writeJson(name: string, data: unknown) {
  402 |   fs.writeFileSync(path.join(OUT, name), JSON.stringify(data, null, 2), 'utf8')
  403 | }
  404 | 
  405 | const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  406 |   { name: '1280x900', width: 1280, height: 900 },
  407 |   { name: '950x760', width: 950, height: 760 },
  408 |   { name: '900x760', width: 900, height: 760 },
  409 |   { name: '900x640', width: 900, height: 640 },
  410 |   { name: '900x639', width: 900, height: 639 },
  411 |   { name: '899x760', width: 899, height: 760 },
  412 |   { name: '700x760', width: 700, height: 760 },
  413 |   { name: '640x400', width: 640, height: 400 },
  414 |   { name: '500x400', width: 500, height: 400 },
  415 |   { name: '400x400', width: 400, height: 400 },
  416 |   { name: '360x400', width: 360, height: 400 },
  417 | ]
  418 | 
  419 | test.describe.configure({ mode: 'serial' })
  420 | 
  421 | test.describe('image-data-detail v0.3 isolated acceptance', () => {
  422 |   let store: Store
  423 | 
  424 |   test.beforeEach(async ({ context }) => {
  425 |     store = defaultStore()
  426 |     NETLOG.length = 0
  427 |     UNHANDLED.length = 0
  428 |     await installNetwork(context, store)
  429 |   })
  430 | 
  431 |   test.afterEach(async () => {
  432 |     writeJson('network-log-last.json', { count: NETLOG.length, unhandled: UNHANDLED, sample: NETLOG.slice(0, 40), unhandledApi: store?.calls.unhandledApi })
  433 |   })
  434 | 
  435 |   test('P0: 类别与标注状态横排/裁切/对齐与选中值', async ({ page }) => {
  436 |     const results: unknown[] = []
  437 |     await page.setViewportSize({ width: 1280, height: 900 })
  438 |     await openLibrary(page)
  439 |     const dialog = await openEditor(page)
  440 | 
  441 |     async function capture(tag: string) {
  442 |       await expect(dialog.locator('.ie-category-fields')).toBeVisible()
  443 |       const measured = await measureCategoryLabel(page)
  444 |       const shotPath = await shot(page, `P0-${tag}`)
  445 |       const row = { tag, screenshot: shotPath, measured }
  446 |       results.push(row)
  447 |       return measured as any
  448 |     }
  449 | 
  450 |     for (const vp of VIEWPORTS) {
  451 |       await page.setViewportSize({ width: vp.width, height: vp.height })
  452 |       await page.waitForTimeout(200)
  453 |       await capture(vp.name)
  454 |     }
  455 | 
  456 |     // Hunt content-box 519 and 520 by adjusting viewport width at height 760.
  457 |     const hunt: unknown[] = []
  458 |     for (let width = 860; width <= 1100; width += 2) {
  459 |       await page.setViewportSize({ width, height: 760 })
  460 |       const measured = await measureCategoryLabel(page) as any
  461 |       const cw = measured.ieScrollContent?.contentWidth ?? null
  462 |       hunt.push({ width, height: 760, contentWidth: cw, horizontal: measured.alignment?.horizontal, stacked: measured.alignment?.stacked, grid: measured.categoryFields?.gridTemplateColumns })
  463 |     }
  464 |     const around520 = (hunt as any[]).filter(row => typeof row.contentWidth === 'number' && Math.abs(row.contentWidth - 520) <= 8)
  465 |     const closest520 = (hunt as any[]).reduce((best, row) => {
  466 |       if (typeof row.contentWidth !== 'number') return best
  467 |       if (!best || Math.abs(row.contentWidth - 520) < Math.abs(best.contentWidth - 520)) return row
  468 |       return best
  469 |     }, null as any)
  470 |     const closest519 = (hunt as any[]).reduce((best, row) => {
  471 |       if (typeof row.contentWidth !== 'number') return best
  472 |       if (!best || Math.abs(row.contentWidth - 519) < Math.abs(best.contentWidth - 519)) return row
  473 |       return best
  474 |     }, null as any)
  475 | 
  476 |     async function captureExact(target: number, found: any, tag: string) {
  477 |       if (!found) return null
  478 |       await page.setViewportSize({ width: found.width, height: 760 })
  479 |       await page.waitForTimeout(200)
  480 |       const measured = await capture(`${tag}-w${found.width}`)
  481 |       return { target, foundWidth: found.width, measuredContent: (measured as any).ieScrollContent, alignment: (measured as any).alignment, grid: (measured as any).categoryFields?.gridTemplateColumns }
  482 |     }
  483 |     const at520 = await captureExact(520, closest520, 'content520')
  484 |     const at519 = await captureExact(519, closest519, 'content519')
  485 | 
  486 |     // Selected-value correctness + draft vs saved snapshot, at a wide horizontal viewport.
  487 |     await page.setViewportSize({ width: 1280, height: 900 })
  488 |     await page.waitForTimeout(200)
  489 |     const before = await capture('1280x900-values-before')
  490 |     const leftCategoryBefore = (before as any).savedRail.find((row: any) => row.dt === '类别')?.dd
  491 |     const leftStatusBefore = (before as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd
> 492 |     await dialog.getByRole('combobox', { name: '类别' }).fill('红烧')
      |                                                        ^ TimeoutError: locator.fill: Timeout 12000ms exceeded.
  493 |     await dialog.getByRole('option', { name: /^红烧肉/ }).click()
  494 |     await expect(dialog.getByRole('radio', { name: '已核验' })).toBeChecked()
  495 |     const afterPick = await capture('1280x900-after-pick-hongshao')
  496 |     const leftCategoryAfter = (afterPick as any).savedRail.find((row: any) => row.dt === '类别')?.dd
  497 |     const leftStatusAfter = (afterPick as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd
  498 |     await dialog.getByRole('radio', { name: '自动标注' }).check()
  499 |     const afterRadio = await capture('1280x900-after-radio-auto')
  500 | 
  501 |     // Long path image at the same wide viewport (new dialog).
  502 |     await dialog.getByRole('button', { name: '撤销修改' }).click()
  503 |     await dialog.getByRole('button', { name: '关闭弹窗' }).click()
  504 |     const longDialog = await openEditor(page, '标注 长路径图')
  505 |     await expect(longDialog.locator('.ie-category-fields')).toBeVisible()
  506 |     const longWide = await (async () => {
  507 |       const measured = await measureCategoryLabel(page)
  508 |       const shotPath = await shot(page, 'P0-1280x900-long-path')
  509 |       results.push({ tag: '1280x900-long-path', screenshot: shotPath, measured })
  510 |       return measured as any
  511 |     })()
  512 |     await page.setViewportSize({ width: 700, height: 760 })
  513 |     await page.waitForTimeout(200)
  514 |     const longNarrow = await (async () => {
  515 |       const measured = await measureCategoryLabel(page)
  516 |       const shotPath = await shot(page, 'P0-700x760-long-path')
  517 |       results.push({ tag: '700x760-long-path', screenshot: shotPath, measured })
  518 |       return measured as any
  519 |     })()
  520 | 
  521 |     const summary = {
  522 |       fixtureNote: '全部为隔离测试夹具数据，不是真实业务库。主图为煎牛排（类别 菜品/肉类/牛排，标注状态 自动标注）；另有超长类别路径图。',
  523 |       viewportMatrix: results,
  524 |       contentBoxHunt: { around520, closest519, closest520, at519, at520, rawCount: hunt.length },
  525 |       valueChecks: {
  526 |         before: {
  527 |           leftCategory: leftCategoryBefore,
  528 |           leftStatus: leftStatusBefore,
  529 |           selectedName: (before as any).selectedStrong?.text,
  530 |           selectedPath: (before as any).selectedPath?.text,
  531 |           selectedCode: (before as any).selectedCode?.text,
  532 |           radios: (before as any).radios,
  533 |         },
  534 |         afterPick: {
  535 |           leftCategory: leftCategoryAfter,
  536 |           leftStatus: leftStatusAfter,
  537 |           selectedName: (afterPick as any).selectedStrong?.text,
  538 |           selectedPath: (afterPick as any).selectedPath?.text,
  539 |           selectedCode: (afterPick as any).selectedCode?.text,
  540 |           radios: (afterPick as any).radios,
  541 |           leftUnchangedCategory: leftCategoryAfter === leftCategoryBefore,
  542 |           leftUnchangedStatus: leftStatusAfter === leftStatusBefore,
  543 |         },
  544 |         afterRadio: {
  545 |           leftStatus: (afterRadio as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd,
  546 |           radios: (afterRadio as any).radios,
  547 |           leftStillAuto: (afterRadio as any).savedRail.find((row: any) => row.dt === '标注状态')?.dd === '自动标注',
  548 |         },
  549 |         longPath: {
  550 |           wide: { selectedName: longWide.selectedStrong?.text, selectedPath: longWide.selectedPath?.text, clipping: longWide.clipping, selfOverflow: longWide.selfOverflow, alignment: longWide.alignment },
  551 |           narrow: { selectedName: longNarrow.selectedStrong?.text, selectedPath: longNarrow.selectedPath?.text, clipping: longNarrow.clipping, selfOverflow: longNarrow.selfOverflow, alignment: longNarrow.alignment },
  552 |         },
  553 |       },
  554 |       unhandled: UNHANDLED,
  555 |       apiUnhandled: store.calls.unhandledApi,
  556 |     }
  557 |     writeJson('P0-category-label-measurements.json', summary)
  558 |   })
  559 | 
  560 |   test('A-D: v0.3 17项真实窗口测量与行为（隔离夹具）', async ({ page }) => {
  561 |     const evidence: Record<string, unknown> = { items: {}, shots: [], networkUnhandled: [] }
  562 |     const record = (id: number, data: unknown) => { (evidence.items as any)[id] = data }
  563 | 
  564 |     await page.setViewportSize({ width: 1280, height: 900 })
  565 |     await openLibrary(page)
  566 |     const dialog = await openEditor(page)
  567 | 
  568 |     // Item 1: accessible name, isolate other surfaces later
  569 |     const accName = await dialog.evaluate(el => el.getAttribute('aria-labelledby') && (document.getElementById(el.getAttribute('aria-labelledby')!)?.textContent || ''))
  570 |     const titleText = await dialog.locator('.dialog-heading h2').innerText()
  571 |     await shot(page, 'I01-library-dialog')
  572 |     record(1, { titleText, accName, passHint: titleText === '图片标注' })
  573 | 
  574 |     // Item 5,6,7,9 at 1280x900 and 900x760
  575 |     async function layoutPack(tag: string) {
  576 |       const pack = await page.evaluate(() => {
  577 |         const box = (sel: string) => {
  578 |           const el = document.querySelector(sel) as HTMLElement | null
  579 |           if (!el) return null
  580 |           const r = el.getBoundingClientRect()
  581 |           const cs = getComputedStyle(el)
  582 |           return { x: r.x, y: r.y, w: r.width, h: r.height, overflow: `${cs.overflowX}/${cs.overflowY}`, position: cs.position, display: cs.display, gridTemplateColumns: cs.gridTemplateColumns, inlineSize: cs.inlineSize, blockSize: cs.blockSize, width: cs.width, height: cs.height }
  583 |         }
  584 |         const content = (sel: string) => {
  585 |           const el = document.querySelector(sel) as HTMLElement | null
  586 |           if (!el) return null
  587 |           const cs = getComputedStyle(el)
  588 |           const pl = parseFloat(cs.paddingLeft) || 0, pr = parseFloat(cs.paddingRight) || 0, pt = parseFloat(cs.paddingTop) || 0, pb = parseFloat(cs.paddingBottom) || 0
  589 |           return { clientWidth: el.clientWidth, clientHeight: el.clientHeight, contentWidth: el.clientWidth - pl - pr, contentHeight: el.clientHeight - pt - pb, padding: [pt, pr, pb, pl], scrollbarGutter: cs.scrollbarGutter, borderBoxW: el.getBoundingClientRect().width, borderBoxH: el.getBoundingClientRect().height }
  590 |         }
  591 |         const groups = [...document.querySelectorAll('.ie-rail .ie-meta-group')].map(sec => ({
  592 |           label: sec.getAttribute('aria-label'),
```