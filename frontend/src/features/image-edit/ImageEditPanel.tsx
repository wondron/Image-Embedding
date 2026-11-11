import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Check, LoaderCircle, RefreshCw, RotateCcw, Trash2, UploadCloud } from 'lucide-react'
import { ApiError } from '../../shared/api'
import { formatLibraryTime } from '../../shared/lib'
import { enumValueText, useEnumCatalog } from '../../entities/enum'
import {
  FOOD_STATE_ENUM,
  LICENSE_ENUM,
  MAX_DESCRIPTION_LENGTH,
  SOURCE_TYPE_ENUM,
  charLength,
  deleteImage,
  foodFieldsFilled,
  getImage,
  payloadDifferences,
  pushImageVector,
  updateImage,
  type ImageDeleteResult,
  type ImageDetailResult,
  type ImageVectorState,
  type LibraryImageItem,
  type VectorStatus,
  type VectorSyncOutcome,
} from '../../entities/image'
import { FoodFields, TagInput } from '../annotation-fields'
import { CategoryPicker, categoryPath, type CategoryTreeState } from '../category-picker'
import { useSceneCatalog } from '../scene-catalog'
import { SceneMembershipPanel } from '../scene-memberships/SceneMembershipPanel'
import { describePatch, draftFromImage, isDirty, toImagePatch, validateDraft, withCategory, type ImageEditDraft } from './editDraft'
import './image-edit.css'

const LABEL_TEXT = { unlabeled: '未标注', auto: '自动标注', verified: '已核验' } as const

/** What a write's vector_sync means for the person (same wording as the scene membership panel). */
const SAVED: Record<VectorSyncOutcome, string> = {
  pushed: '已保存，检索已生效。',
  missing: '已保存。这张图在当前向量库里没有向量，检索查不到它，需要重新入库。',
  not_ready: '已保存到数据库；检索服务未就绪，检索要等同步后才能看到这次修改。',
  failed: '已保存到数据库；更新检索索引失败，可稍后点「重推 payload」。',
}
const PUSHED: Record<VectorSyncOutcome, string> = {
  pushed: '已重推，向量库和数据库一致了。',
  missing: '这张图在当前向量库里没有向量，无法重推，需要重新入库。',
  not_ready: '检索服务未就绪，暂时不能重推。',
  failed: '重推失败，请稍后再试（服务端日志有原因）。',
}
const VECTOR_VIEW: Record<VectorStatus, { label: string; help: string }> = {
  synced: { label: '已同步', help: '检索按这里保存的类别、场景和食物状态过滤。' },
  stale: { label: '待同步', help: '向量库里的过滤信息落后于数据库，检索结果可能不准，可以重推。' },
  missing: { label: '没有向量', help: '当前模型的向量库里没有这张图，检索查不到它，需要重新入库。' },
  unavailable: { label: '无法读取', help: '' },
}
const PAYLOAD_FIELDS: Record<string, string> = { category_id: '类别', scenes: '场景', food_state: '食物状态', label_status: '标注状态' }

type Notice = { tone: 'ok' | 'warn' | 'error'; text: string }

function failure(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.traceId ? ` · trace_id=${error.traceId}` : ''}`
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}
const notFound = (error: unknown) => error instanceof ApiError && error.code === 'IMAGE_NOT_FOUND'
const payloadValue = (value: unknown) => Array.isArray(value) ? value.join('、') || '（空）' : value === null || value === undefined ? '（空）' : String(value)

export interface ImageEditPanelProps {
  item: LibraryImageItem
  apiBase?: string
  /** The category tree (shared with the page's filter). */
  categories: CategoryTreeState
  /** After any write: reload the library page. */
  onChanged: () => void
  /** The image was deleted (or found gone); the parent closes the dialog. */
  onDeleted: (result: ImageDeleteResult | null) => void
  /** Open the scene management sync tab. */
  onManage?: () => void
  /** Unsaved edits: the parent keeps the dialog open until they are saved or undone. */
  onDirtyChange?: (dirty: boolean) => void
}

/**
 * One gallery image: what is stored (GET /api/images/{id}), its state in the vector store with a
 * re-push, the editable fields (PATCH, only what changed), the scene membership panel, and delete.
 */
export function ImageEditPanel({
  item, apiBase = '', categories, onChanged, onDeleted, onManage, onDirtyChange,
}: ImageEditPanelProps) {
  const base = useId()
  const { catalog } = useEnumCatalog()
  const scenes = useSceneCatalog(apiBase)
  const [detail, setDetail] = useState<ImageDetailResult | null>(null)
  const [draft, setDraft] = useState<ImageEditDraft | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<null | 'save' | 'push' | 'delete'>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [membershipKey, setMembershipKey] = useState(0)
  const [foodOpen, setFoodOpen] = useState(false)
  const generation = useRef(0)
  // The parent's callbacks may change on every render: reading them through refs keeps load stable
  const onChangedRef = useRef(onChanged)
  const onDeletedRef = useRef(onDeleted)
  onChangedRef.current = onChanged
  onDeletedRef.current = onDeleted
  const gone = useCallback((result: ImageDeleteResult | null) => {
    onChangedRef.current()
    onDeletedRef.current(result)
  }, [])

  const image = detail?.image ?? null
  const errors = image && draft ? validateDraft(draft, image, categories.tree) : {}
  const invalid = Object.keys(errors).length > 0
  const dirty = Boolean(image && draft && isDirty(image, draft))
  const patch = image && draft && !invalid ? toImagePatch(image, draft) : {}
  const changes = describePatch(patch)

  /** reset: also start the draft over from what is stored. */
  const load = useCallback(async (reset: boolean) => {
    const current = ++generation.current
    setLoading(true)
    try {
      const result = await getImage(item.id, { baseUrl: apiBase })
      if (current !== generation.current) return
      setDetail(result)
      setLoadError(null)
      if (reset) {
        setDraft(draftFromImage(result.image))
        setFoodOpen(foodFieldsFilled(draftFromImage(result.image)) > 0)
      }
    } catch (cause) {
      if (current !== generation.current) return
      if (notFound(cause)) { gone(null); return }
      setLoadError(failure(cause))
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [apiBase, item.id, gone])

  useEffect(() => {
    setDetail(null); setDraft(null); setNotice(null); setConfirmDelete(false); setLoadError(null)
    void load(true)
    return () => { generation.current++ }
  }, [load])

  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])

  async function save() {
    if (!image || !draft || busy || invalid || !Object.keys(patch).length) return
    setBusy('save'); setNotice(null)
    try {
      const result = await updateImage(image.id, patch, { baseUrl: apiBase })
      setDetail(current => current ? { ...current, image: result.image } : current)
      setDraft(draftFromImage(result.image))
      setNotice({ tone: result.vectorSync === 'pushed' ? 'ok' : 'warn', text: result.vectorSync ? SAVED[result.vectorSync] : '已保存到数据库。' })
      setMembershipKey(key => key + 1)  // a new category may move rule memberships
      onChangedRef.current()
      void load(false)  // the vector state after the push
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      setNotice({ tone: 'error', text: `保存失败：${failure(cause)}` })
      // A timeout or a 5xx may still have saved: show what is stored, keep the edits
      if (!(cause instanceof ApiError) || cause.status === null || cause.status >= 500) void load(false)
    } finally {
      setBusy(null)
    }
  }

  async function push() {
    if (!image || busy) return
    setBusy('push'); setNotice(null)
    try {
      const result = await pushImageVector(image.id, { baseUrl: apiBase })
      setNotice({ tone: result.vectorSync === 'pushed' ? 'ok' : 'warn', text: result.vectorSync ? PUSHED[result.vectorSync] : '已请求重推。' })
      void load(false)
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      setNotice({ tone: 'error', text: `重推失败：${failure(cause)}` })
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!image || busy) return
    setBusy('delete'); setNotice(null)
    try {
      const result = await deleteImage(image.id, { baseUrl: apiBase })
      gone(result)
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      setNotice({ tone: 'error', text: `删除失败：${failure(cause)}` })
      setConfirmDelete(false)
      setBusy(null)
    }
  }

  const sceneName = (code: string) => scenes.data?.scenes.find(scene => scene.code === code)?.name ?? code
  const preview = image?.originalUrl ?? image?.imageUrl ?? item.originalUrl ?? item.imageUrl
  const shownPath = image?.categoryId != null && categories.tree ? categoryPath(categories.tree, image.categoryId) : null
  const descriptionLength = draft ? charLength(draft.description.trim()) : 0

  return <div className="ie">
    <div className="ie-top">
      <figure className="ie-figure">
        {preview ? <img src={preview} alt="" /> : <span className="ie-noimage">暂无预览</span>}
      </figure>
      <div className="ie-facts">
        <dl className="ie-meta">
          <div><dt>类别</dt><dd>{image ? (image.categoryId === null ? '未标注' : shownPath ?? image.category ?? image.categoryCode ?? `#${image.categoryId}`) : item.category ?? '—'}</dd></div>
          <div><dt>标注状态</dt><dd>{image?.labelStatus ? LABEL_TEXT[image.labelStatus] : '—'}</dd></div>
          <div><dt>所属场景</dt><dd>{(image?.scenes ?? item.scenes).map(sceneName).join('、') || '—'}</dd></div>
          <div><dt>尺寸</dt><dd>{image?.width && image.height ? `${image.width} × ${image.height}` : '—'}</dd></div>
          <div><dt>来源 · 许可</dt><dd>{image ? `${enumValueText(catalog, SOURCE_TYPE_ENUM, image.sourceType)} · ${enumValueText(catalog, LICENSE_ENUM, image.license)}` : '—'}</dd></div>
          <div><dt>入库时间</dt><dd>{formatLibraryTime(image?.createdAt ?? item.createdAt)}</dd></div>
          <div className="ie-meta-wide"><dt>图片 ID</dt><dd className="monospace">{item.id}</dd></div>
        </dl>
        {detail && <VectorCard vector={detail.vector} busy={busy !== null || loading} onPush={() => void push()} onReload={() => void load(false)} />}
      </div>
    </div>

    {loading && !detail && <p className="ie-status" role="status"><LoaderCircle size={15} className="spin" />正在读取图片详情…</p>}
    {loadError && <p className="ie-alert" role="alert">读取图片详情失败：{loadError} <button type="button" className="mg-text-button" onClick={() => void load(!draft)}><RotateCcw size={13} />重试</button></p>}

    {image && draft && <form className="ia-form ie-form" onSubmit={event => { event.preventDefault(); void save() }} aria-label="编辑标注">
      <section className="ia-section" aria-labelledby={`${base}-category-title`}>
        <h3 id={`${base}-category-title`}>类别与标注状态</h3>
        <CategoryPicker
          id={`${base}-category`}
          label="类别"
          tree={categories.tree}
          loading={categories.loading}
          loadError={categories.error}
          onRetry={categories.refresh}
          value={draft.categoryId}
          onChange={categoryId => setDraft(withCategory(draft, image, categoryId))}
          disabled={busy !== null}
          error={errors.category}
          hint={draft.categoryId === null ? '没有类别时图片为「未标注」。' : undefined}
        />
        {draft.categoryId !== null && <fieldset className="ia-choice" disabled={busy !== null}>
          <legend>标注状态</legend>
          <label><input type="radio" name={`${base}-label`} checked={draft.labelStatus === 'auto'} onChange={() => setDraft({ ...draft, labelStatus: 'auto' })} />自动标注</label>
          <label><input type="radio" name={`${base}-label`} checked={draft.labelStatus === 'verified'} onChange={() => setDraft({ ...draft, labelStatus: 'verified' })} />已核验</label>
          <small>换了类别默认记为「已核验」：是人在图库里选的。</small>
        </fieldset>}
      </section>

      <section className="ia-section" aria-labelledby={`${base}-text-title`}>
        <h3 id={`${base}-text-title`}>标签与描述</h3>
        <div className="ia-field">
          <label htmlFor={`${base}-tags`}>标签</label>
          <TagInput id={`${base}-tags`} tags={draft.tags} onChange={tags => setDraft({ ...draft, tags })} disabled={busy !== null} error={errors.tags} />
          {errors.tags && <small className="ia-field-error" role="alert">{errors.tags}</small>}
        </div>
        <div className="ia-field">
          <label htmlFor={`${base}-description`}>描述</label>
          <textarea id={`${base}-description`} rows={3} disabled={busy !== null} value={draft.description} aria-invalid={errors.description ? true : undefined} onChange={event => setDraft({ ...draft, description: event.target.value })} />
          <small className={`ia-counter${errors.description ? ' is-over' : ''}`}>{errors.description ?? `${descriptionLength} / ${MAX_DESCRIPTION_LENGTH}`}</small>
        </div>
      </section>

      <details className="ia-section ia-collapsible" open={foodOpen} onToggle={event => setFoodOpen(event.currentTarget.open)}>
        <summary><h3>食物标注</h3><small>{foodFieldsFilled(draft) ? `已填 ${foodFieldsFilled(draft)} 项 · ${enumValueText(catalog, FOOD_STATE_ENUM, draft.foodState || null)}` : '未填写'}</small></summary>
        <FoodFields idBase={base} value={draft} onChange={change => setDraft({ ...draft, ...change })} errors={errors} disabled={busy !== null} />
        <p className="ia-muted">食物状态参与检索过滤，改了会同步到向量库；其余字段只存在数据库里。</p>
      </details>
    </form>}

    <div className="ie-memberships">
      <SceneMembershipPanel key={membershipKey} imageId={item.id} apiBase={apiBase} onSaved={() => { onChangedRef.current(); void load(false) }} onManage={onManage} />
    </div>

    <div className="ie-bar">
      {notice && <p className={`ie-notice is-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>{notice.text}</p>}
      {confirmDelete
        ? <div className="ie-ask" role="alertdialog" aria-label="确认删除">
          <p>删除后图库和检索结果里都看不到这张图片，向量和原图、缩略图也会清理，无法恢复。</p>
          <div><button type="button" className="mg-btn mg-secondary" disabled={busy === 'delete'} onClick={() => setConfirmDelete(false)}>取消</button><button type="button" className="mg-btn ie-danger" disabled={busy === 'delete'} onClick={() => void remove()}>{busy === 'delete' ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}确认删除</button></div>
        </div>
        : <div className="ie-actions">
          <button type="button" className="mg-text-button ie-delete" disabled={!image || busy !== null} onClick={() => setConfirmDelete(true)}><Trash2 size={14} />删除图片</button>
          <span className="ie-changes" title={changes}>{invalid ? '有需要修改的内容' : dirty ? `未保存：${changes || '修改'}（保存或撤销后可关闭）` : ''}</span>
          <button type="button" className="mg-btn mg-secondary" disabled={!dirty || busy !== null} onClick={() => image && setDraft(draftFromImage(image))}>撤销修改</button>
          <button type="button" className="mg-btn mg-primary" disabled={!dirty || invalid || busy !== null || !changes} onClick={() => void save()}>{busy === 'save' ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}保存修改</button>
        </div>}
    </div>
  </div>
}

function VectorCard({ vector, busy, onPush, onReload }: { vector: ImageVectorState; busy: boolean; onPush: () => void; onReload: () => void }) {
  const view = VECTOR_VIEW[vector.status]
  const differences = vector.status === 'stale' ? payloadDifferences(vector) : []
  return <section className={`ie-vector is-${vector.status}`} aria-label="向量库状态">
    <div className="ie-vector-head">
      <strong>向量库</strong>
      <span className="ie-vector-badge">{view.label}</span>
      {vector.status === 'stale' && <button type="button" className="mg-text-button" disabled={busy} onClick={onPush}><UploadCloud size={13} />重推 payload</button>}
      {vector.status === 'unavailable' && <button type="button" className="mg-text-button" disabled={busy} onClick={onReload}><RefreshCw size={13} />刷新</button>}
    </div>
    <p>{view.help || vector.detail || '读不到这张图在向量库里的状态。'}</p>
    {differences.length > 0 && <ul className="ie-diff">{differences.map(([key, stored, expected]) => <li key={key}>
      <span>{PAYLOAD_FIELDS[key] ?? key}</span>库中 {payloadValue(stored)}，应为 {payloadValue(expected)}
    </li>)}</ul>}
    {vector.collection && <small>{vector.embeddingSpaceId ?? vector.collection} · payload v{vector.payloadVersion ?? '—'} · 已同步 v{vector.syncedVersion ?? '—'}</small>}
  </section>
}
