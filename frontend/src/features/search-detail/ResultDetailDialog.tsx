import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  ArrowDownToLine, ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, Copy, FolderOpen, LoaderCircle, PencilLine, TriangleAlert,
} from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { useEnumValueText } from '../../entities/enum'
import {
  FOOD_STATE_ENUM,
  LABEL_STATUS_TEXT,
  ProgressiveImage,
  storedImageSources,
  type ImageDeleteResult,
  type ImageDetail,
  type LibraryImageItem,
} from '../../entities/image'
import { SourceBadge, getThumbnailSources, hasImage, type SearchChannel, type SearchImage, type SearchRegion } from '../../entities/search-result'
import { useApiBase } from '../../shared/api'
import { notify } from '../../shared/ui'
import { categoryPath, type CategoryTreeState } from '../category-picker'
import { ImageEditPanel } from '../image-edit'
import { useSceneCatalog } from '../scene-catalog'
import { ImageUnavailable, QueryRegionPreview, RecipeMark, ResultEditedMark, ResultSceneLabel, isLibraryHit } from '../search'
import { RESULT_TAG_SEPARATOR, isEditableResult } from './resultEdits'
import './result-detail.css'

/** What the detail calls a result's channel; null when the answer did not say (现网 Global's own search). */
function channelText(source?: SearchChannel, region?: SearchRegion): string | null {
  switch (source) {
    case 'self': return region ? '自建 · 区域搜索（整图没有达到阈值的结果）' : '自建'
    case 'recipe': return '菜谱识别（查询图里识别出的菜名）'
    case 'global': return '现网 Global（业务搜图兜底）'
    case 'empty': return '无结果'
    case 'flyb': return 'FlyB 兜底'
    default: return null
  }
}

/** The card as the gallery editor takes it (it reads the stored image itself; these fill in while it loads). */
function libraryItem(item: SearchImage): LibraryImageItem {
  return {
    id: item.id,
    imageUrl: item.thumbnailUrl,
    originalUrl: item.imageUrl,
    categoryId: item.categoryId,
    category: item.category,
    scenes: item.scenes,
    sceneMemberships: item.scenes.map(code => ({ code })),
    labelStatus: item.labelStatus,
    description: item.description,
    tags: item.tags ? item.tags.split(RESULT_TAG_SEPARATOR) : [],
    foodState: item.foodState,
    heatingGroup: item.heatingGroup,
    width: item.width,
    height: item.height,
    createdAt: null,
    updatedAt: null,
    vectorStatus: null,
  }
}

/** The result's image: the thumbnail the list already loaded at once, the original over it once loaded. */
function ResultPhoto({ item }: { item: SearchImage }) {
  const apiBase = useApiBase()
  const self = item.source === 'self'
  return <ProgressiveImage
    previewSources={self ? storedImageSources([item.thumbnailUrl], item.id, 'thumb', apiBase) : getThumbnailSources(item)}
    fullSources={self ? storedImageSources([item.imageUrl], item.id, 'original', apiBase) : item.imageUrl ? [item.imageUrl] : []}
    alt={item.title}
    placeholder={<ImageUnavailable />}
  />
}

function SimilarityMeter({ value }: { value: number | null }) {
  if (value === null) {
    return <div className="rd-score is-unknown"><span>相似度</span><b>未返回分数</b></div>
  }
  const share = Math.max(0, Math.min(1, value))
  return <div className="rd-score">
    <span>相似度</span>
    <b>{(value * 100).toFixed(1)}%</b>
    <span className="rd-score-track" aria-hidden><span style={{ inlineSize: `${share * 100}%` }} /></span>
  </div>
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <div className={wide ? 'is-wide' : undefined}><dt>{label}</dt><dd>{children}</dd></div>
}

const Empty = ({ children }: { children: ReactNode }) => <span className="rd-empty">{children}</span>

/** 加热组 and 食物状态, for scenes that output heating information. */
function HeatingRows({ item }: { item: SearchImage }) {
  const foodState = useEnumValueText(FOOD_STATE_ENUM, item.foodState)
  return <>
    <Field label="加热组">{item.heatingGroup?.name ?? <Empty>—</Empty>}</Field>
    <Field label="食物状态">{item.foodState ? foodState : <Empty>—</Empty>}</Field>
  </>
}

export interface ResultDetailDialogProps {
  /** The result shown, with any annotation saved since the search. */
  item: SearchImage
  /** The results as listed (filters and order applied), to step through with ← / →. */
  list: readonly SearchImage[]
  /** Show another result of the list. */
  onShow: (item: SearchImage) => void
  /** Its annotation was changed after the search. */
  edited: boolean
  /** The answer outputs heating information (自建 says it on the envelope, 业务搜图 per card). */
  outputsHeating: boolean
  /** The scene searched: an edited image may have left it. */
  scene: { code: string; name: string } | null
  /** The query image, to draw a region hit's box on (业务搜图). */
  queryImage: File | string | null
  apiBase: string
  /** The category tree: the category's path here, the picker in the editor. */
  categories: CategoryTreeState
  /** The id of the image being downloaded, if any. */
  downloading: string | null
  onDownload: (item: SearchImage) => void
  onSaveOptions: (item: SearchImage) => void
  /** 菜谱识别's card: the button to 模型工具 · 菜谱识别 with the query. */
  recipeTool: ReactNode
  /** The editor opens. */
  onEditStart: () => void
  /** The editor wrote the image: `card` is the result it was opened from. */
  onStored: (card: SearchImage, image: ImageDetail) => void
  /** The image was deleted in the editor (null: it was gone already). */
  onDeleted: (card: SearchImage, result: ImageDeleteResult | null) => void
  /** Scene management, on its sync tab. */
  onManageScenes: () => void
  /** 上传与导入: uploading the image again restores a missing vector. */
  onUpload: () => void
  onClose: () => void
}

/**
 * A search result in detail: the photo on the left (← / → step through the listed results), what
 * the result says on the right. A self-built hit is a stored image: 「编辑标注」 opens the gallery's
 * editor in the same dialog, and what it saves shows on the result at once.
 */
export function ResultDetailDialog(props: ResultDetailDialogProps) {
  const { item, list, onShow, edited, apiBase, categories, downloading, onClose } = props
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  /** The editor has unsaved edits: the dialog stays open, and on this image, until they are saved or undone. */
  const [dirty, setDirty] = useState(false)
  // Another result starts with its details
  const [shownId, setShownId] = useState(item.id)
  if (shownId !== item.id) {
    setShownId(item.id)
    setMode('view')
    setDirty(false)
  }
  const frame = useRef<HTMLDivElement>(null)
  const sceneCatalog = useSceneCatalog(apiBase)

  const image = hasImage(item)
  const library = isLibraryHit(item)
  const editable = isEditableResult(item)
  const channel = channelText(item.source, item.region)
  const editing = mode === 'edit' && editable
  const index = list.findIndex(entry => entry.id === item.id)
  const previous = !editing && index > 0 ? list[index - 1] : null
  const next = !editing && index >= 0 && index < list.length - 1 ? list[index + 1] : null

  // ← / →: the previous or next result, unless a field, or another dialog over this one, has the keys
  useEffect(() => {
    if (!previous && !next) return
    function step(event: KeyboardEvent) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing) return
      const dialog = frame.current?.closest('dialog')
      const target = event.target
      if (!dialog || !(target instanceof Element)) return
      if (target !== target.ownerDocument.body && !dialog.contains(target)) return
      if (target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return
      if (Array.from(dialog.ownerDocument.querySelectorAll('dialog[open]')).some(other => other !== dialog && other.matches(':modal'))) return
      const to = event.key === 'ArrowLeft' ? previous : next
      if (!to) return
      event.preventDefault()
      onShow(to)
    }
    document.addEventListener('keydown', step)
    return () => document.removeEventListener('keydown', step)
  }, [previous, next, onShow])

  function startEdit() {
    props.onEditStart()
    setMode('edit')
  }

  async function copyId() {
    try {
      await navigator.clipboard.writeText(item.id)
      notify.success('已复制图片 ID。')
    } catch {
      notify.error('复制失败：浏览器不允许访问剪贴板，请手动选中 ID 复制。', { title: '没有复制' })
    }
  }

  const title = editing ? '编辑标注' : image ? '图片详情' : '菜谱识别结果'
  const footer = editing ? undefined : <footer className="rd-footer">
    <p className="rd-footnote">{!image
      ? '这是菜谱识别在查询图里识别出的菜名，不是图库里的图片，没有可下载的图片。'
      : library
        ? editable ? '图库里的图片：标注有误时点「编辑标注」修改，保存后检索和这里的结果随之更新。' : '这条结果没有图片 ID，不能在这里修改标注。'
        : '图片来自外部来源，请根据来源授权使用。'}</p>
    <div className="rd-actions">
      <button type="button" className="btn btn-secondary" onClick={onClose}>关闭</button>
      {!library && item.pageUrl && <a className="btn btn-secondary" href={item.pageUrl} target="_blank" rel="noreferrer">来源网页<ArrowUpRight size={13} /></a>}
      {image && <button type="button" className="btn btn-secondary" onClick={() => props.onSaveOptions(item)}><FolderOpen size={14} />保存选项</button>}
      {image && <button type="button" className={`btn ${editable ? 'btn-secondary' : 'btn-primary'}`} disabled={Boolean(downloading)} onClick={() => props.onDownload(item)}>
        {downloading === item.id ? <LoaderCircle size={14} className="spin" /> : <ArrowDownToLine size={14} />}下载图片
      </button>}
      {editable && <button type="button" className="btn btn-primary" onClick={startEdit}><PencilLine size={14} />编辑标注</button>}
    </div>
  </footer>

  return <Dialog title={title} size="lg" className="result-detail" onClose={onClose} dismissDisabled={dirty} footer={footer}>
    {editing
      ? <div className="rd-edit" ref={frame}>
        <div className="rd-edit-bar">
          <button type="button" className="text-button rd-back" disabled={dirty} title={dirty ? '有未保存的修改：保存或撤销后再返回' : undefined} onClick={() => setMode('view')}>
            <ArrowLeft size={14} />返回详情
          </button>
          <span>改的是图库里这张图片的标注：保存后同步到检索，这次搜索结果里的卡片也随之更新。</span>
        </div>
        <ImageEditPanel
          item={libraryItem(item)}
          apiBase={apiBase}
          categories={categories}
          onChanged={() => {}}
          onDeleted={result => props.onDeleted(item, result)}
          onDirtyChange={setDirty}
          onStored={stored => props.onStored(item, stored)}
          onManage={props.onManageScenes}
          onUpload={dirty ? undefined : props.onUpload}
        />
      </div>
      : <div className="rd" ref={frame}>
        <section className="rd-stage" aria-label="图片">
          <div className="rd-photo">
            {image ? <ResultPhoto key={item.id} item={item} /> : <RecipeMark />}
            {previous && <button type="button" className="rd-step is-previous" onClick={() => onShow(previous)} aria-label="上一张" title="上一张（←）"><ChevronLeft size={20} /></button>}
            {next && <button type="button" className="rd-step is-next" onClick={() => onShow(next)} aria-label="下一张" title="下一张（→）"><ChevronRight size={20} /></button>}
          </div>
          <StageBar item={item} index={index} total={list.length} apiBase={apiBase} />
        </section>

        <div className="rd-info">
          <DetailHead item={item} edited={edited} categories={categories} />
          {image && <SimilarityMeter value={item.similarity} />}
          {edited && props.scene && library && !item.scenes.includes(props.scene.code) && <p className="rd-warning" role="note">
            <TriangleAlert size={14} aria-hidden />修改后这张图已不在「{props.scene.name}」场景里，再搜这个场景时不会出现。
          </p>}

          {library && <section className="rd-section" aria-label="标注">
            <h4>标注</h4>
            <dl className="rd-fields">
              <Field label="场景标签"><ResultSceneLabel value={item.sceneLabel} /></Field>
              <Field label="标签">{item.tags || <Empty>无标签</Empty>}</Field>
              <Field label="描述">{item.description.trim() ? <p className="rd-description">{item.description}</p> : <Empty>无描述</Empty>}</Field>
              <Field label="所属场景">{item.scenes.length
                ? <span className="rd-chips">{item.scenes.map(code => <span className="rd-chip" key={code}>{sceneCatalog.data?.scenes.find(scene => scene.code === code)?.name ?? code}</span>)}</span>
                : <Empty>—</Empty>}</Field>
              {(item.outputsHeating || props.outputsHeating) && <HeatingRows item={item} />}
            </dl>
          </section>}

          {image && !library && <section className="rd-section" aria-label="来源">
            <h4>来源</h4>
            <dl className="rd-fields">
              <Field label="描述">{item.description.trim() ? <p className="rd-description">{item.description}</p> : <Empty>无描述</Empty>}</Field>
              <Field label="来源站点">{item.siteName ?? <Empty>未提供</Empty>}</Field>
              <Field label="网页地址">{item.pageUrl
                ? <a className="rd-link" href={item.pageUrl} target="_blank" rel="noreferrer" title={item.pageUrl}>{item.pageUrl}</a>
                : <Empty>未提供</Empty>}</Field>
            </dl>
          </section>}

          {/* 业务搜图's region search: which box of the query image found this hit */}
          {item.region && <section className="rd-section" aria-label="命中区域">
            <h4>命中区域</h4>
            <p className="rd-note">整图没有达到阈值的结果，这张图是用查询图里检测到的「{item.region.label || '目标'}」区域搜到的（检测置信度 {item.region.score.toFixed(2)}，位置 {item.region.box.join(', ')}，{item.region.box[2] - item.region.box[0]} × {item.region.box[3] - item.region.box[1]} 像素）。</p>
            {props.queryImage && <QueryRegionPreview source={props.queryImage} region={item.region} />}
          </section>}

          {/* 菜谱识别's card: how the names came about, and where to see it box by box */}
          {item.source === 'recipe' && <section className="rd-section" aria-label="识别方式">
            <h4>识别方式</h4>
            <p className="rd-note">GroundingDINO 在查询图里找到的每个框裁出来交给菜谱识别模型，和底库最相近的菜相似度高于 RECIPE_DETECT_CONFIG.threshold 才算识别出来；每个框最多一个菜名，同名只留一个，按相似度从高到低。整图搜索有结果时不会有这张卡。</p>
            <div className="rd-tool">{props.recipeTool}</div>
          </section>}

          {(channel || editable) && <section className="rd-section" aria-label="检索">
            <h4>检索</h4>
            <dl className="rd-fields">
              {channel && <Field label="检索通道">{channel}</Field>}
              {editable && <Field label="图片 ID"><span className="rd-id">
                <code title={item.id}>{item.id}</code>
                <button type="button" className="rd-copy" onClick={() => void copyId()} aria-label="复制图片 ID" title="复制图片 ID"><Copy size={13} /></button>
              </span></Field>}
            </dl>
          </section>}
        </div>
      </div>}
  </Dialog>
}

/** Badges, the heading (the category of a library hit, else the title) and what places it. */
function DetailHead({ item, edited, categories }: { item: SearchImage; edited: boolean; categories: CategoryTreeState }) {
  const library = isLibraryHit(item)
  const heading = library ? item.category ?? '未分类' : item.title
  const path = library && item.categoryId !== null && categories.tree ? categoryPath(categories.tree, item.categoryId) : null
  // A library hit: where its category sits in the tree; a global result: the site it came from
  const sub = library ? (path && path !== heading ? path : null) : item.siteName
  return <header className="rd-head">
    <div className="rd-badges">
      <SourceBadge source={item.source} region={item.region} />
      {library && item.labelStatus && <span className={`rd-status is-${item.labelStatus}`}>{LABEL_STATUS_TEXT[item.labelStatus]}</span>}
      {edited && <ResultEditedMark />}
    </div>
    <h3 className={`rd-title${library && !item.category ? ' is-empty' : ''}`}>{heading}</h3>
    {sub && <p className="rd-sub" title={sub}>{sub}</p>}
  </header>
}

/** Under the photo: its size, the original in a new tab, and where the result is in the list. */
function StageBar({ item, index, total, apiBase }: { item: SearchImage; index: number; total: number; apiBase: string }) {
  if (!hasImage(item)) return total > 1 && index >= 0 ? <div className="rd-stage-bar"><span className="rd-position">{index + 1} / {total}</span></div> : null
  const original = item.source === 'self'
    ? storedImageSources([item.imageUrl], item.id, 'original', apiBase)[0] ?? null
    : item.imageUrl ?? item.thumbnailUrl
  return <div className="rd-stage-bar">
    <span>{item.width && item.height ? `${item.width} × ${item.height} px` : '尺寸未知'}</span>
    {original && <a href={original} target="_blank" rel="noreferrer" title="在新标签页打开原图，可另存为">原图<ArrowUpRight size={12} /></a>}
    {total > 1 && <span className="rd-position">{index >= 0 ? `${index + 1} / ${total}` : '已不在当前筛选结果里'}</span>}
  </div>
}
