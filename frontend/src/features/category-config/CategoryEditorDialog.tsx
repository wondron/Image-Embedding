import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { LoaderCircle, Plus, X } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import {
  categoryDraftFromNode,
  categoryLabel,
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
import { saveFailureText } from './messages'

export type CategoryEditorTarget =
  | { mode: 'create'; parent: CategoryNode | null }
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
   * Sends the request. Resolves null once the server accepted it (the caller then unmounts this
   * dialog), otherwise the failure to show here.
   */
  save: (request: CategorySaveRequest) => Promise<CategoryFailureView | null>
  onClose: () => void
}

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
  const parent = target.mode === 'create' ? target.parent : null
  const creatingRoot = target.mode === 'create' && target.parent === null

  const entries = useMemo(() => flattenCategoryTree(tree), [tree])
  const takenCodes = useMemo(
    () => new Set(entries.filter(entry => entry.node.id !== node?.id).map(entry => entry.node.code)),
    [entries, node],
  )
  // A node cannot move under itself or its own subtree.
  const forbiddenParentIds = useMemo(() => (node ? subtreeIds(tree, node.id) : new Set<number>()), [tree, node])
  const parentOptions = useMemo(
    () => entries.filter(entry => !forbiddenParentIds.has(entry.node.id)),
    [entries, forbiddenParentIds],
  )
  const freeRootCodes = useMemo(() => rootCodes.filter(code => !takenCodes.has(code)), [rootCodes, takenCodes])
  const rootAllowed = node !== null && (node.parentId === null || rootCodes.includes(node.code))

  const [draft, setDraft] = useState<CategoryDraft>(() => {
    if (node) return categoryDraftFromNode(node)
    if (parent) return emptyCategoryDraft(parent)
    const code = freeRootCodes[0] ?? ''
    return { ...emptyCategoryDraft(null, code), zhName: domainName(code) ?? '' }
  })
  const [fieldErrors, setFieldErrors] = useState<CategoryFieldErrors>({})
  const [notice, setNotice] = useState<{ text: string; tone: 'error' | 'status' } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [focusField, setFocusField] = useState<CategoryFieldName | null>(null)
  const submitLock = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  // A new child opens with its parent's code as a prefix ("dish."): put the caret after it once,
  // so typing continues the code. (Dialog has focused the field already: child effects run first.)
  useEffect(() => {
    if (!parent) return
    const input = document.getElementById('category-form-code')
    if (input instanceof HTMLInputElement) input.setSelectionRange(input.value.length, input.value.length)
  }, [parent])

  useEffect(() => {
    if (!focusField) return
    const element = document.getElementById(`category-form-${focusField}`)
    const focusable = 'input, select, textarea, button'
    const control = element?.matches(focusable) ? element : element?.querySelector<HTMLElement>(focusable)
    control?.focus()
    setFocusField(null)
  }, [focusField])

  function edit(field: CategoryFieldName | null, changes: Partial<CategoryDraft>) {
    setDraft(current => ({ ...current, ...changes }))
    if (field && fieldErrors[field]) {
      setFieldErrors(current => {
        const next = { ...current }
        delete next[field]
        return next
      })
    }
  }

  function chooseRootCode(code: string) {
    // Follow the domain with its name until the user types a name of their own.
    const typed = draft.zhName.trim()
    const followsDomain = typed === '' || typed === (domainName(draft.code) ?? '')
    edit('code', followsDomain ? { code, zhName: domainName(code) ?? '' } : { code })
  }

  function editName(index: number, changes: Partial<CategoryName>) {
    edit('otherNames', {
      otherNames: draft.otherNames.map((entry, at) => (at === index ? { ...entry, ...changes } : entry)),
    })
  }

  function showFailure(failure: CategoryFailureView) {
    if (Object.keys(failure.fieldErrors).length > 0) {
      setFieldErrors(failure.fieldErrors)
      setFocusField(firstField(failure.fieldErrors))
      setNotice(null)
      return
    }
    setNotice({ text: saveFailureText(failure), tone: 'error' })
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitLock.current) return
    const validated = validateCategoryDraft(draft, {
      rootCodes,
      takenCodes,
      original: node ?? undefined,
      forbiddenParentIds,
    })
    if (!validated.ok) {
      setFieldErrors(validated.fieldErrors)
      setFocusField(firstField(validated.fieldErrors))
      setNotice(null)
      return
    }
    let request: CategorySaveRequest
    if (node) {
      const body = toCategoryUpdateBody(node, validated.payload)
      if (!body) {
        setFieldErrors({})
        setNotice({ text: '没有改动，无需保存。', tone: 'status' })
        return
      }
      request = { mode: 'edit', id: node.id, body }
    } else {
      request = { mode: 'create', body: toCategoryCreateBody(validated.payload) }
    }
    submitLock.current = true
    setSubmitting(true)
    setFieldErrors({})
    setNotice(null)
    try {
      const failure = await save(request)
      if (failure && mounted.current) showFailure(failure)
    } finally {
      submitLock.current = false
      if (mounted.current) setSubmitting(false)
    }
  }

  const title = node ? '编辑类别' : creatingRoot ? '新建根类别' : '新建子类别'
  const describedBy = (field: CategoryFieldName, hint?: string) => (
    fieldErrors[field] ? `category-form-${field}-error` : hint
  )
  const rootCodeFixed = node !== null && draft.parentId === null

  return <Dialog title={title} size="lg" dismissDisabled={submitting} onClose={onClose}>
    <form className="mg-editor cat-form" onSubmit={event => void submit(event)} aria-busy={submitting} noValidate>
      <div className="mg-editor-body">
        <section className="mg-editor-section" aria-labelledby="category-form-place-title">
          <h3 id="category-form-place-title">位置与编码</h3>
          {node
            ? <label className="mg-field" htmlFor="category-form-parentId"><span className="mg-field-label">上级类别</span>
              <select
                id="category-form-parentId"
                value={draft.parentId ?? ''}
                disabled={submitting}
                aria-invalid={Boolean(fieldErrors.parentId)}
                aria-describedby={describedBy('parentId', 'category-form-parentId-hint')}
                onChange={event => edit('parentId', { parentId: event.target.value === '' ? null : Number(event.target.value) })}
              >
                {rootAllowed && <option value="">（根节点）</option>}
                {parentOptions.map(entry => <option key={entry.node.id} value={entry.node.id}>
                  {`${'　'.repeat(entry.depth)}${categoryLabel(entry.node)}（${entry.node.code}）`}
                </option>)}
              </select>
              <small id="category-form-parentId-hint">换上级会把它下面的子类别一起移过去。</small>
              <FieldError field="parentId" errors={fieldErrors} />
            </label>
            : <div className="mg-field"><span className="mg-field-label">上级类别</span>
              <p className="cat-form-static">{parent ? `${categoryLabel(parent)}（${parent.code}）` : '（根节点）'}</p>
            </div>}

          <label className="mg-field" htmlFor="category-form-code"><span className="mg-field-label">编码<span aria-hidden="true"> *</span></span>
            {creatingRoot
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
                data-dialog-initial-focus={node ? undefined : true}
                value={draft.code}
                disabled={submitting || rootCodeFixed}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={Boolean(fieldErrors.code)}
                aria-describedby={describedBy('code', 'category-form-code-hint')}
                onChange={event => edit('code', { code: event.target.value })}
              />}
            <small id="category-form-code-hint">{creatingRoot || rootCodeFixed
              ? '根类别只能使用域的编码。'
              : '小写字母、数字和下划线，用点分段，例如 dish.pizza。入库时按编码引用类别。'}</small>
            <FieldError field="code" errors={fieldErrors} />
          </label>
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
              onChange={event => edit('zhName', { zhName: event.target.value })}
            />
            <small id="category-form-zhName-hint">检索结果和图库里显示的类别名。</small>
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
                  onClick={() => edit('otherNames', { otherNames: draft.otherNames.filter((_, at) => at !== index) })}
                ><X size={15} /></button>
              </div>)}
              <button
                type="button"
                className="mg-text-button"
                disabled={submitting}
                onClick={() => edit('otherNames', { otherNames: [...draft.otherNames, { lang: 'zh', name: '', kind: 'alias' }] })}
              ><Plus size={14} />添加名称</button>
            </div>
            <small>中文别名，或其他语言的名称（语言代码如 en、ja）。每种语言最多一个主名。</small>
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
              placeholder="chinese, american"
              aria-invalid={Boolean(fieldErrors.cuisines)}
              aria-describedby={describedBy('cuisines', 'category-form-cuisines-hint')}
              onChange={event => edit('cuisines', { cuisines: event.target.value })}
            />
            <small id="category-form-cuisines-hint">多个用逗号分隔；留空则沿用上级的菜系。场景规则按菜系匹配。</small>
            <FieldError field="cuisines" errors={fieldErrors} />
          </label>
          <label className="mg-field" htmlFor="category-form-heatingGroupId"><span className="mg-field-label">加热组</span>
            <select
              id="category-form-heatingGroupId"
              value={draft.heatingGroupId ?? ''}
              disabled={submitting}
              aria-invalid={Boolean(fieldErrors.heatingGroupId)}
              aria-describedby={describedBy('heatingGroupId', 'category-form-heatingGroupId-hint')}
              onChange={event => edit('heatingGroupId', { heatingGroupId: event.target.value === '' ? null : Number(event.target.value) })}
            >
              <option value="">不设置（沿用上级）</option>
              {heatingGroups.map(group => <option key={group.id} value={group.id}>{`${group.name}（${group.code}）`}</option>)}
            </select>
            <small id="category-form-heatingGroupId-hint">{heatingGroups.length > 0
              ? '只在一体机等输出加热信息的场景随结果给出。'
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
              onChange={event => edit('sortOrder', { sortOrder: event.target.value })}
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
                onChange={event => edit(null, { isActive: event.target.checked })}
              />启用
            </label>
            <small>停用的类别仍保留在树里，它的图片和子类别不受影响。</small>
          </div>
        </section>
        {notice && <p className={notice.tone === 'status' ? 'cat-status' : 'mg-form-error'} role={notice.tone === 'status' ? 'status' : 'alert'}>{notice.text}</p>}
      </div>
      <div className="mg-editor-actions">
        <button type="button" className="mg-btn mg-secondary" onClick={onClose} disabled={submitting}>取消</button>
        <button type="submit" className="mg-btn mg-primary" disabled={submitting || (creatingRoot && freeRootCodes.length === 0)}>
          {submitting && <LoaderCircle size={14} className="spin" />}
          {submitting ? '保存中…' : node ? '保存' : '创建'}
        </button>
      </div>
    </form>
  </Dialog>
}
