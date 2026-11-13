import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { LoaderCircle, Plus, X } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import {
  CATEGORY_ROOT_LIMIT,
  categoryCodeUnderParent,
  planCategoryCodeChanges,
  categoryDraftFromNode,
  categoryLabel,
  effectiveCuisines,
  effectiveHeatingGroup,
  emptyCategoryDraft,
  flattenCategoryTree,
  subtreeIds,
  toCategoryCreateBody,
  toCategoryUpdateBody,
  validateCategoryDraft,
  type CategoryDraft,
  type CategoryFailureView,
  type CategoryFieldErrors,
  type CategoryFieldName,
  type CategoryName,
  type CategoryNode,
  type CategoryTree,
  type HeatingGroupRef,
} from '../../entities/category'
import type { CategoryCreateRequestWire, CategoryUpdateRequestWire } from '../../shared/api'
import { formatLibraryTime } from '../../shared/lib'
import { notify } from '../../shared/ui'
import { CategorySearch, categoryPath } from '../category-picker'
import { retargetCode } from './tableModel'
import { saveFailureText } from './messages'
import { sameNameCategories } from './sameName'

export type CategoryEditorTarget =
  /** parentId null: a root (a domain); undefined: the parent is chosen in the form. */
  | { mode: 'create'; parentId?: number | null }
  | { mode: 'edit'; node: CategoryNode }

export type CategorySaveRequest =
  | { mode: 'create'; body: CategoryCreateRequestWire }
  | { mode: 'edit'; id: number; body: CategoryUpdateRequestWire }

interface Props {
  target: CategoryEditorTarget
  tree: CategoryTree
  heatingGroups: readonly HeatingGroupRef[]
  /** Codes a root may use (GET /api/categories root_codes). */
  rootCodes: readonly string[]
  /** The Chinese name of a root code, when the enum catalog has one. */
  domainName: (code: string) => string | null
  /**
   * Sends the request. Resolves null once the server accepted it, otherwise the failure to show
   * here. The caller then unmounts this dialog, unless `keepOpen` asks to stay for the next
   * category under the same parent.
   */
  save: (request: CategorySaveRequest, keepOpen?: boolean) => Promise<CategoryFailureView | null>
  onClose: () => void
}

/** What the parent picker calls "no parent". */
const ROOT_LABEL = '（根节点 · 类别域）'
/** Same-name categories listed under the name field before 「等 N 个」. */
const SAME_NAME_SHOWN = 3

const FIELD_ORDER: readonly CategoryFieldName[] = [
  'parentId', 'code', 'zhName', 'otherNames', 'cuisines', 'heatingGroupId', 'sortOrder',
]

function firstField(errors: CategoryFieldErrors): CategoryFieldName | null {
  return FIELD_ORDER.find(field => errors[field]) ?? null
}

function FieldError({ field, errors }: { field: CategoryFieldName; errors: CategoryFieldErrors }) {
  const message = errors[field]
  return message ? <p className="cat-field-error" id={`category-form-${field}-error`} role="alert">{message}</p> : null
}

export function CategoryEditorDialog({ target, tree, heatingGroups, rootCodes, domainName, save, onClose }: Props) {
  const node = target.mode === 'edit' ? target.node : null
  const presetParentId = target.mode === 'create' ? target.parentId : undefined

  const entries = useMemo(() => flattenCategoryTree(tree), [tree])
  const takenCodes = useMemo(
    () => new Set(entries.filter(entry => entry.node.id !== node?.id).map(entry => entry.node.code.toLowerCase())),
    [entries, node],
  )
  // A node cannot move under itself or its own subtree: the parent picker leaves those out.
  const forbiddenParentIds = useMemo(() => (node ? subtreeIds(tree, node.id) : new Set<number>()), [tree, node])
  const freeRootCodes = useMemo(() => rootCodes.filter(code => !takenCodes.has(code.toLowerCase())), [rootCodes, takenCodes])
  const rootCapacityAvailable = tree.roots.length < CATEGORY_ROOT_LIMIT
  // Only a domain may sit at the top: an existing root, or a node whose code is a domain's.
  const rootOffered = node
    ? node.parentId === null || (rootCapacityAvailable && rootCodes.some(code => code.toLowerCase() === categoryCodeUnderParent(node.code, null).toLowerCase()))
    : rootCapacityAvailable && freeRootCodes.length > 0

  const prefixOf = (parentId: number | null) => {
    const parent = parentId === null ? undefined : tree.byId.get(parentId)?.node
    return parent ? `${parent.code}.` : ''
  }

  const [parentPicked, setParentPicked] = useState(node !== null || presetParentId !== undefined)
  const [draft, setDraft] = useState<CategoryDraft>(() => {
    if (node) return categoryDraftFromNode(node)
    if (presetParentId === undefined) return emptyCategoryDraft(null)
    const parent = presetParentId === null ? undefined : tree.byId.get(presetParentId)?.node
    if (parent) return emptyCategoryDraft(parent)
    const code = freeRootCodes[0] ?? ''
    return { ...emptyCategoryDraft(null, code), zhName: domainName(code) ?? '' }
  })
  const [fieldErrors, setFieldErrors] = useState<CategoryFieldErrors>({})
  const [submitting, setSubmitting] = useState(false)
  /** Categories created with 「创建并继续」 while this dialog stayed open. */
  const [createdCount, setCreatedCount] = useState(0)
  const [focusField, setFocusField] = useState<CategoryFieldName | null>(null)
  const submitLock = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  /**
   * A new child's code starts as its parent's code plus a dot ("dish."). While that is all it
   * holds, focusing the field puts the caret after it, so typing continues the code instead of
   * replacing it (Tab would select the whole value). Only here, in the focus event: a later
   * change could undo a selection the user made on purpose.
   */
  function keepPrefix(input: HTMLInputElement) {
    if (node || !parentPicked || draft.parentId === null) return
    const prefix = prefixOf(draft.parentId)
    if (input.value.toLowerCase() === prefix.toLowerCase()) input.setSelectionRange(prefix.length, prefix.length)
  }

  useEffect(() => {
    if (!focusField) return
    const element = document.getElementById(`category-form-${focusField}`)
    const focusable = 'input, select, textarea, button'
    const control = element?.matches(focusable) ? element : element?.querySelector<HTMLElement>(focusable)
    control?.focus()
    setFocusField(null)
  }, [focusField])

  function edit(fields: readonly CategoryFieldName[], changes: Partial<CategoryDraft>) {
    setDraft(current => ({ ...current, ...changes }))
    if (fields.some(field => fieldErrors[field])) {
      setFieldErrors(current => {
        const next = { ...current }
        for (const field of fields) delete next[field]
        return next
      })
    }
  }

  const rootMode = parentPicked && draft.parentId === null
  const moving = node !== null && draft.parentId !== node.parentId
  const codeForSave = moving
    ? categoryCodeUnderParent(draft.code, draft.parentId === null ? null : tree.byId.get(draft.parentId)?.node.code ?? '')
    : draft.code.trim()
  const rewritingCodes = node !== null && (moving || codeForSave !== node.code)
  const codePlan = useMemo(
    () => node && rewritingCodes ? planCategoryCodeChanges(tree, node.id, codeForSave) : [],
    [tree, node, rewritingCodes, codeForSave],
  )
  const changedCodes = codePlan.filter(change => change.from !== change.to)
  const validationTakenCodes = useMemo(() => new Set(
    entries.filter(entry => entry.node.id !== node?.id && !(rewritingCodes && forbiddenParentIds.has(entry.node.id)))
      .map(entry => entry.node.code),
  ), [entries, node, rewritingCodes, forbiddenParentIds])

  /** parentId null: the top level (a domain). */
  function chooseParent(parentId: number | null) {
    if (node) {
      const code = categoryCodeUnderParent(draft.code, parentId === null ? null : tree.byId.get(parentId)?.node.code ?? '')
      edit(['parentId', 'code'], { parentId, code })
      return
    }
    const wasRoot = rootMode
    const domainNameNow = wasRoot ? domainName(draft.code) ?? '' : null
    const followsDomain = draft.zhName.trim() === '' || draft.zhName.trim() === domainNameNow
    if (parentId === null) {
      // A root: its code is one of the free domain codes, its name follows the domain's
      const code = freeRootCodes.find(code => code.toLowerCase() === draft.code.toLowerCase()) ?? freeRootCodes[0] ?? ''
      edit(['parentId', 'code', 'zhName'], { parentId, code, ...(followsDomain ? { zhName: domainName(code) ?? '' } : {}) })
    } else {
      // What was typed after the old parent's prefix moves under the new one
      const code = wasRoot ? prefixOf(parentId) : retargetCode(draft.code, parentPicked ? prefixOf(draft.parentId) : '', prefixOf(parentId))
      edit(['parentId', 'code'], { parentId, code, ...(wasRoot && followsDomain ? { zhName: '' } : {}) })
    }
    setParentPicked(true)
  }

  function chooseRootCode(code: string) {
    // Follow the domain with its name until the user types a name of their own.
    const typed = draft.zhName.trim()
    const followsDomain = typed === '' || typed === (domainName(draft.code) ?? '')
    edit(['code'], followsDomain ? { code, zhName: domainName(code) ?? '' } : { code })
  }

  function editName(index: number, changes: Partial<CategoryName>) {
    edit(['otherNames'], {
      otherNames: draft.otherNames.map((entry, at) => (at === index ? { ...entry, ...changes } : entry)),
    })
  }

  function showFieldErrors(errors: CategoryFieldErrors) {
    setFieldErrors(errors)
    setFocusField(firstField(errors))
  }

  function showFailure(failure: CategoryFailureView) {
    if (Object.keys(failure.fieldErrors).length > 0) showFieldErrors(failure.fieldErrors)
    else notify.error(saveFailureText(failure), { title: '没有保存' })
  }

  /** After 「创建并继续」: the same parent, everything else blank again, ready for the next code. */
  function startNext() {
    const parent = draft.parentId === null ? null : tree.byId.get(draft.parentId)?.node ?? null
    setDraft(emptyCategoryDraft(parent))
    setFieldErrors({})
    setCreatedCount(count => count + 1)
    setFocusField('code')
  }

  /** keepOpen (creating only): once created, stay open for the next category under the same parent. */
  async function submit(event: FormEvent | null, keepOpen = false) {
    event?.preventDefault()
    if (submitLock.current) return
    if (!parentPicked) {
      showFieldErrors({ parentId: '请选择上级类别。' })
      return
    }
    const validated = validateCategoryDraft({ ...draft, code: codeForSave }, {
      rootCodes,
      rootCount: tree.roots.length,
      takenCodes: validationTakenCodes,
      original: node ?? undefined,
      forbiddenParentIds,
      codePlan,
    })
    if (!validated.ok) {
      showFieldErrors(validated.fieldErrors)
      return
    }
    let request: CategorySaveRequest
    if (node) {
      const body = toCategoryUpdateBody(node, validated.payload)
      if (!body) {
        setFieldErrors({})
        notify.info('没有改动，无需保存。')
        return
      }
      request = { mode: 'edit', id: node.id, body }
    } else {
      request = { mode: 'create', body: toCategoryCreateBody(validated.payload) }
    }
    submitLock.current = true
    setSubmitting(true)
    setFieldErrors({})
    try {
      const failure = await save(request, keepOpen)
      if (!mounted.current) return
      if (failure) showFailure(failure)
      else if (keepOpen) startNext()
    } finally {
      submitLock.current = false
      if (mounted.current) setSubmitting(false)
    }
  }

  const describedBy = (field: CategoryFieldName, hint?: string) => (
    fieldErrors[field] ? `category-form-${field}-error` : hint
  )
  // A root keeps its domain code; so does a node moved to the top (only a domain-coded one may be)
  const rootCodeFixed = node !== null && draft.parentId === null
  const pickedParent = parentPicked && draft.parentId !== null ? tree.byId.get(draft.parentId)?.node ?? null : null
  const pickedParentPath = pickedParent ? categoryPath(tree, pickedParent.id) : null
  // With hundreds of categories it is easy to add one that exists elsewhere, or as somebody's alias
  const sameName = useMemo(() => sameNameCategories(tree, draft.zhName, node?.id), [tree, draft.zhName, node])

  // What an empty cuisines / heating group field falls back to: the new parent's own, else its ancestors'
  const parentNode = draft.parentId === null ? undefined : tree.byId.get(draft.parentId)?.node
  const inheritedCuisines = parentNode ? effectiveCuisines(tree, parentNode.id) : null
  const cuisinesFrom = inheritedCuisines?.value.length ? inheritedCuisines.from ?? parentNode! : null
  const inheritedGroup = parentNode ? effectiveHeatingGroup(tree, parentNode.id) : null
  const groupFrom = inheritedGroup?.value ? inheritedGroup.from ?? parentNode! : null
  const childCount = node ? tree.byId.get(node.id)?.children.length ?? 0 : 0

  return <Dialog title={node ? '编辑类别' : '新建类别'} size="lg" dismissDisabled={submitting} onClose={onClose}>
    <form className="mg-editor cat-form" onSubmit={event => void submit(event)} aria-busy={submitting} noValidate>
      <div className="mg-editor-body">
        {node && <p className="mg-editor-note ct-editor-meta">
          {[
            `${childCount} 个下级类别`,
            `${node.imageCount} 张图片`,
            `${node.sceneRuleCount} 条场景规则`,
            node.updatedAt ? `更新于 ${formatLibraryTime(node.updatedAt)}` : null,
          ].filter(Boolean).join(' · ')}
        </p>}
        <section className="mg-editor-section" aria-labelledby="category-form-place-title">
          <h3 id="category-form-place-title">位置与编码</h3>
          <div className="mg-field">
            <label className="mg-field-label" htmlFor="category-form-parentId">上级类别{!node && <span aria-hidden="true"> *</span>}</label>
            <div className={`cat-parent${fieldErrors.parentId ? ' is-invalid' : ''}`}>
              {parentPicked && <div className="cp-selected cat-parent-current">
                {draft.parentId === null
                  ? <div><strong>根节点 · 类别域</strong><small>最顶层，没有上级</small></div>
                  : <div>
                    <strong>{pickedParent ? categoryLabel(pickedParent) : `类别 #${draft.parentId}`}</strong>
                    {pickedParent && pickedParentPath !== categoryLabel(pickedParent) && <small>{pickedParentPath}</small>}
                    {pickedParent && <code>{pickedParent.code}</code>}
                    {pickedParent && !pickedParent.isActive && <small className="cp-inactive">已停用</small>}
                  </div>}
              </div>}
              {/* The whole tree, searchable and folded by level; the node itself and what is below it are left out */}
              <CategorySearch
                id="category-form-parentId"
                label="上级类别"
                tree={tree}
                value={parentPicked ? draft.parentId : undefined}
                onPick={chooseParent}
                disabled={submitting}
                includeInactive
                excludeIds={forbiddenParentIds}
                nullOption={rootOffered ? ROOT_LABEL : undefined}
                placeholder={parentPicked ? '搜索以更换上级类别' : '搜索名称、编码或别名，或在下面逐级展开'}
                describedBy={describedBy('parentId', 'category-form-parentId-hint')}
                invalid={Boolean(fieldErrors.parentId)}
                dialogInitialFocus={!node && !parentPicked}
              />
            </div>
            <small id="category-form-parentId-hint">{node
              ? '换上级会移动整棵子树，并按新父级的完整编码更新当前节点及所有下级编码。'
              : rootCapacityAvailable
                ? '新类别放在哪个类别下面；只有类别域（食材、菜品等）可以放在最顶层。'
                : '已达到六个根类别的上限，请在现有类别下添加。'}</small>
            <FieldError field="parentId" errors={fieldErrors} />
          </div>

          <label className="mg-field" htmlFor="category-form-code"><span className="mg-field-label">编码<span aria-hidden="true"> *</span></span>
            {!node && rootMode
              ? <select
                id="category-form-code"
                data-dialog-initial-focus
                value={draft.code}
                disabled={submitting || freeRootCodes.length === 0}
                aria-invalid={Boolean(fieldErrors.code)}
                aria-describedby={describedBy('code', 'category-form-code-hint')}
                onChange={event => chooseRootCode(event.target.value)}
              >
                {freeRootCodes.map(code => <option key={code} value={code}>
                  {domainName(code) ? `${domainName(code)}（${code}）` : code}
                </option>)}
              </select>
              : <input
                id="category-form-code"
                data-dialog-initial-focus={!node && parentPicked ? true : undefined}
                value={draft.code}
                disabled={submitting || rootCodeFixed || !parentPicked}
                placeholder={parentPicked ? undefined : '先选择上级类别'}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={Boolean(fieldErrors.code)}
                aria-describedby={describedBy('code', 'category-form-code-hint')}
                onFocus={event => keepPrefix(event.currentTarget)}
                onBlur={() => { if (moving && codeForSave !== draft.code) edit(['code'], { code: codeForSave }) }}
                onChange={event => edit(['code'], { code: event.target.value })}
              />}
            <small id="category-form-code-hint">{rootMode || rootCodeFixed
              ? '根类别只能使用类别域的编码，重复检查不区分大小写。'
              : '大小写字母、数字和下划线，用点分段，例如 ingredient.HongSu。保留大小写，重复检查不区分大小写。'}</small>
            <FieldError field="code" errors={fieldErrors} />
          </label>
          {changedCodes.length > 0 && <div className="mg-editor-note cat-code-preview" role="status" aria-label="编码更新预览">
            <p>保存后将一起更新 {changedCodes.length} 个节点的编码，保留每个节点最后一段的大小写。</p>
            <ul>{changedCodes.slice(0, 5).map(change => <li key={change.id}>
              {change.name}：<code>{change.from}</code> → <code>{change.to}</code>
            </li>)}</ul>
            {changedCodes.length > 5 && <p>另有 {changedCodes.length - 5} 个下级节点会按同样规则更新。</p>}
          </div>}
        </section>

        <section className="mg-editor-section" aria-labelledby="category-form-names-title">
          <h3 id="category-form-names-title">名称</h3>
          <label className="mg-field" htmlFor="category-form-zhName"><span className="mg-field-label">中文名称<span aria-hidden="true"> *</span></span>
            <input
              id="category-form-zhName"
              data-dialog-initial-focus={node ? true : undefined}
              value={draft.zhName}
              disabled={submitting}
              autoComplete="off"
              aria-invalid={Boolean(fieldErrors.zhName)}
              aria-describedby={describedBy('zhName', 'category-form-zhName-hint')}
              onChange={event => edit(['zhName'], { zhName: event.target.value })}
            />
            <small id="category-form-zhName-hint">检索结果和图库里显示的类别名。</small>
            {sameName.length > 0 && <p className="cat-same-name" role="status">
              已有同名的类别：{sameName.slice(0, SAME_NAME_SHOWN).map(match => (
                `${match.path}${match.alias === null ? '' : `（别名「${match.alias}」）`}`
              )).join('；')}{sameName.length > SAME_NAME_SHOWN ? ` 等 ${sameName.length} 个` : ''}。名称可以重复，确认不是同一种再保存。
            </p>}
            <FieldError field="zhName" errors={fieldErrors} />
          </label>
          <div className="mg-field" role="group" aria-labelledby="category-form-otherNames-label">
            <span className="mg-field-label" id="category-form-otherNames-label">其他名称</span>
            <div className="cat-names" id="category-form-otherNames">
              {draft.otherNames.map((entry, index) => <div className="cat-name-row" key={index}>
                <input
                  className="cat-name-lang"
                  aria-label={`第 ${index + 1} 行语言代码`}
                  placeholder="en"
                  value={entry.lang}
                  disabled={submitting}
                  spellCheck={false}
                  autoComplete="off"
                  onChange={event => editName(index, { lang: event.target.value.toLowerCase() })}
                />
                <input
                  aria-label={`第 ${index + 1} 行名称`}
                  value={entry.name}
                  disabled={submitting}
                  autoComplete="off"
                  onChange={event => editName(index, { name: event.target.value })}
                />
                <select
                  aria-label={`第 ${index + 1} 行类型`}
                  value={entry.kind}
                  disabled={submitting}
                  onChange={event => editName(index, { kind: event.target.value === 'primary' ? 'primary' : 'alias' })}
                >
                  <option value="alias">别名</option>
                  <option value="primary">主名</option>
                </select>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`删除第 ${index + 1} 行名称`}
                  disabled={submitting}
                  onClick={() => edit(['otherNames'], { otherNames: draft.otherNames.filter((_, at) => at !== index) })}
                ><X size={15} /></button>
              </div>)}
              <button
                type="button"
                className="mg-text-button"
                disabled={submitting}
                onClick={() => edit(['otherNames'], { otherNames: [...draft.otherNames, { lang: 'zh', name: '', kind: 'alias' }] })}
              ><Plus size={14} />添加名称</button>
            </div>
            <small>中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名；搜索时别名也能找到它。</small>
            <FieldError field="otherNames" errors={fieldErrors} />
          </div>
        </section>

        <section className="mg-editor-section" aria-labelledby="category-form-attributes-title">
          <h3 id="category-form-attributes-title">属性</h3>
          <label className="mg-field" htmlFor="category-form-cuisines"><span className="mg-field-label">菜系</span>
            <input
              id="category-form-cuisines"
              value={draft.cuisines}
              disabled={submitting}
              spellCheck={false}
              autoComplete="off"
              placeholder={cuisinesFrom ? inheritedCuisines!.value.join(', ') : 'chinese, american'}
              aria-invalid={Boolean(fieldErrors.cuisines)}
              aria-describedby={describedBy('cuisines', 'category-form-cuisines-hint')}
              onChange={event => edit(['cuisines'], { cuisines: event.target.value })}
            />
            <small id="category-form-cuisines-hint">{cuisinesFrom && draft.cuisines.trim() === ''
              ? `留空：沿用「${categoryLabel(cuisinesFrom)}」的 ${inheritedCuisines!.value.join('、')}。多个用逗号分隔，场景规则按菜系匹配。`
              : '多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。'}</small>
            <FieldError field="cuisines" errors={fieldErrors} />
          </label>
          <label className="mg-field" htmlFor="category-form-heatingGroupId"><span className="mg-field-label">加热组</span>
            <select
              id="category-form-heatingGroupId"
              value={draft.heatingGroupId ?? ''}
              disabled={submitting}
              aria-invalid={Boolean(fieldErrors.heatingGroupId)}
              aria-describedby={describedBy('heatingGroupId', 'category-form-heatingGroupId-hint')}
              onChange={event => edit(['heatingGroupId'], { heatingGroupId: event.target.value === '' ? null : Number(event.target.value) })}
            >
              <option value="">{groupFrom ? `不设置（沿用「${categoryLabel(groupFrom)}」：${inheritedGroup!.value!.name}）` : '不设置'}</option>
              {heatingGroups.map(group => <option key={group.id} value={group.id}>{`${group.name}（${group.code}）`}</option>)}
            </select>
            <small id="category-form-heatingGroupId-hint">{heatingGroups.length > 0
              ? '只在一体机等输出加热信息的场景随结果给出；不设置则沿用上级的加热组。'
              : '数据库里还没有加热组（heating_groups 表为空）。'}</small>
            <FieldError field="heatingGroupId" errors={fieldErrors} />
          </label>
          <label className="mg-field" htmlFor="category-form-sortOrder"><span className="mg-field-label">同级排序</span>
            <input
              id="category-form-sortOrder"
              type="number"
              step={1}
              value={draft.sortOrder}
              disabled={submitting}
              aria-invalid={Boolean(fieldErrors.sortOrder)}
              aria-describedby={describedBy('sortOrder', 'category-form-sortOrder-hint')}
              onChange={event => edit(['sortOrder'], { sortOrder: event.target.value })}
            />
            <small id="category-form-sortOrder-hint">整数，同一上级下数字小的排在前面。</small>
            <FieldError field="sortOrder" errors={fieldErrors} />
          </label>
          <div className="mg-field"><span className="mg-field-label">状态</span>
            <label className="cat-check">
              <input
                type="checkbox"
                checked={draft.isActive}
                disabled={submitting}
                onChange={event => edit([], { isActive: event.target.checked })}
              />启用
            </label>
            <small>停用后上传和图库编辑里不再列出它；它的图片和下级类别不受影响。</small>
          </div>
        </section>
      </div>
      <div className="mg-editor-actions">
        {createdCount > 0 && <span className="cat-created" role="status">本次已创建 {createdCount} 个</span>}
        <button type="button" className="mg-btn mg-secondary" onClick={onClose} disabled={submitting}>{createdCount > 0 ? '完成' : '取消'}</button>
        {/* Several categories under one parent: no need to reopen the dialog and find the parent again */}
        {!node && parentPicked && !rootMode && <button
          type="button"
          className="mg-btn mg-secondary"
          disabled={submitting}
          title="创建后留在这里，继续在同一个上级下面新建"
          onClick={() => void submit(null, true)}
        >创建并继续</button>}
        <button type="submit" className="mg-btn mg-primary" disabled={submitting || (!node && rootMode && freeRootCodes.length === 0)}>
          {submitting && <LoaderCircle size={14} className="spin" />}
          {submitting ? '保存中…' : node ? '保存' : '创建'}
        </button>
      </div>
    </form>
  </Dialog>
}
