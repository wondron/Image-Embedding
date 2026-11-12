import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Check, LoaderCircle, RotateCcw, Trash2 } from 'lucide-react'
import { ApiError } from '../../shared/api'
import { notify } from '../../shared/ui'
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
  pushImageVector,
  reembedImage,
  updateImage,
  type ImageDeleteResult,
  type ImageDetailResult,
  type LabelStatus,
  type LibraryImageItem,
  type VectorSyncOutcome,
} from '../../entities/image'
import { FoodFields, TagInput } from '../annotation-fields'
import { CategoryPicker, categoryPath, type CategoryTreeState } from '../category-picker'
import { useSceneCatalog } from '../scene-catalog'
import { SceneMembershipPanel } from '../scene-memberships/SceneMembershipPanel'
import { describePatch, draftFromImage, isDirty, toImagePatch, validateDraft, withCategory, type ImageEditDraft } from './editDraft'
import { VectorCard } from './VectorCard'
import type { PayloadNames } from './vectorView'
import './image-edit.css'

const LABEL_TEXT: Record<LabelStatus, string> = { unlabeled: '未标注', auto: '自动标注', verified: '已核验' }

/** What a write's vector_sync means for the person (same wording as the scene membership panel). */
const SAVED: Record<VectorSyncOutcome, string> = {
  pushed: '已保存，向量库已同步，检索已生效。',
  missing: '已保存到数据库。这张图在当前向量库里没有向量，检索查不到它；可以在上方用原图重新生成向量。',
  not_ready: '已保存到数据库；检索服务未就绪，向量库要等它恢复后再同步。',
  failed: '已保存到数据库；写入向量库失败，可以稍后点「按数据库同步到向量库」。',
}
const PUSHED: Record<VectorSyncOutcome, string> = {
  pushed: '已同步，向量库和数据库一致了。',
  missing: '这张图在当前向量库里没有向量，没法同步；可以用原图重新生成向量。',
  not_ready: '检索服务未就绪，暂时不能同步。',
  failed: '同步失败，请稍后再试（服务端日志有原因）。',
}

/** Why the stored original could not become a vector, in words. */
const REEMBED_FAILED: Record<string, string> = {
  IMAGE_FILE_UNAVAILABLE: '读不到存储里的原图（可能已被清理，或对象存储没有保留文件），只能重新上传这张图片。',
  NOT_READY: '检索服务未就绪（编码模型、向量库或对象存储没有启动），暂时不能重新生成向量。',
  INDEX_BAD_IMAGE: '存储里的原图无法识别为图片，只能重新上传这张图片。',
}


/** A write went through: pushed to the search index (success), or not (yet) there (a warning to read). */
function writeOutcome(sync: VectorSyncOutcome | null, text: string, notPushedTitle: string) {
  if (sync === null || sync === 'pushed') notify.success(text)
  else notify.warn(text, { title: notPushedTitle })
}

function failure(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.traceId ? ` · trace_id=${error.traceId}` : ''}`
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}
const notFound = (error: unknown) => error instanceof ApiError && error.code === 'IMAGE_NOT_FOUND'

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
  /** Open 上传与导入 (uploading the same image again restores a missing vector). */
  onUpload?: () => void
  /** Unsaved edits: the parent keeps the dialog open until they are saved or undone. */
  onDirtyChange?: (dirty: boolean) => void
}

/**
 * One gallery image: what is stored (GET /api/images/{id}), its state in the vector store with a
 * re-push, the editable fields (PATCH, only what changed), the scene membership panel, and delete.
 */
export function ImageEditPanel({
  item, apiBase = '', categories, onChanged, onDeleted, onManage, onUpload, onDirtyChange,
}: ImageEditPanelProps) {
  const base = useId()
  const { catalog } = useEnumCatalog()
  const scenes = useSceneCatalog(apiBase)
  const [detail, setDetail] = useState<ImageDetailResult | null>(null)
  const [draft, setDraft] = useState<ImageEditDraft | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<null | 'save' | 'push' | 'reembed' | 'delete'>(null)
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
    setDetail(null); setDraft(null); setConfirmDelete(false); setLoadError(null)
    void load(true)
    return () => { generation.current++ }
  }, [load])

  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])

  /** A write answered with fresh state: a read still in flight started before it and must not overwrite it. */
  function retireReads() {
    generation.current++
    setLoading(false)
  }

  async function save() {
    if (!image || !draft || busy || invalid || !Object.keys(patch).length) return
    setBusy('save')
    try {
      const result = await updateImage(image.id, patch, { baseUrl: apiBase })
      // The response carries the vector state after the push; an older server needs one more read
      retireReads()
      setDetail(current => current ? { ...current, image: result.image, vector: result.vector ?? current.vector } : current)
      setDraft(draftFromImage(result.image))
      writeOutcome(result.vectorSync, result.vectorSync ? SAVED[result.vectorSync] : '已保存到数据库。', '已保存，检索还没更新')
      setMembershipKey(key => key + 1)  // a new category may move rule memberships
      onChangedRef.current()
      if (!result.vector) void load(false)
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      notify.error(failure(cause), { title: '保存失败' })
      // A timeout or a 5xx may still have saved: show what is stored, keep the edits
      if (!(cause instanceof ApiError) || cause.status === null || cause.status >= 500) void load(false)
    } finally {
      setBusy(null)
    }
  }

  async function push() {
    if (!image || busy) return
    setBusy('push')
    try {
      const result = await pushImageVector(image.id, { baseUrl: apiBase })
      writeOutcome(result.vectorSync, result.vectorSync ? PUSHED[result.vectorSync] : '已请求同步。', '没有同步到向量库')
      if (result.vector) {
        const vector = result.vector
        retireReads()
        setDetail(current => current ? { ...current, vector } : current)
      } else {
        void load(false)
      }
      onChangedRef.current()  // the gallery card shows the vector state too
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      notify.error(failure(cause), { title: '同步失败' })
    } finally {
      setBusy(null)
    }
  }

  async function reembed() {
    if (!image || busy) return
    setBusy('reembed')
    try {
      const result = await reembedImage(image.id, { baseUrl: apiBase })
      const vector = result.vector
      retireReads()
      setDetail(current => current ? { ...current, vector } : current)
      if (vector.status === 'synced') notify.success('已用原图重新生成向量，检索可以查到这张图了。')
      else notify.warn('向量已写入，但向量库的状态还不对，可以刷新后再看。')
      onChangedRef.current()
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      notify.error(cause instanceof ApiError && REEMBED_FAILED[cause.code ?? ''] ? REEMBED_FAILED[cause.code!] : failure(cause), { title: '重新生成向量失败' })
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!image || busy) return
    setBusy('delete')
    try {
      const result = await deleteImage(image.id, { baseUrl: apiBase })
      gone(result)
    } catch (cause) {
      if (notFound(cause)) { gone(null); return }
      notify.error(failure(cause), { title: '删除失败' })
      setConfirmDelete(false)
      setBusy(null)
    }
  }

  const sceneName = (code: string) => scenes.data?.scenes.find(scene => scene.code === code)?.name ?? code
  const payloadNames: PayloadNames = {
    category: id => categories.tree ? categoryPath(categories.tree, id) : null,
    scene: sceneName,
    foodState: value => enumValueText(catalog, FOOD_STATE_ENUM, value),
    labelStatus: value => LABEL_TEXT[value as LabelStatus] ?? value,
  }
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
        {detail && <VectorCard
          vector={detail.vector}
          ledger={detail.image.embeddings}
          names={payloadNames}
          busy={busy !== null || loading}
          pushing={busy === 'push'}
          reembedding={busy === 'reembed'}
          onPush={() => void push()}
          onReembed={() => void reembed()}
          onReload={() => void load(false)}
          onUpload={onUpload}
        />}
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

