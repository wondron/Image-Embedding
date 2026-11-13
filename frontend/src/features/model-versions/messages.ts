/**
 * How the model versions read for the person changing the embedding model: status names, what the
 * page suggests next (guideFor), what each version can do now (actionsFor), and refusals in words.
 */
import { ApiError } from '../../shared/api'
import {
  buildActive,
  encoderUsable,
  lostPoints,
  type BuildState,
  type EmbeddingModel,
  type EmbeddingModelStatus,
  type ModelOverview,
  type WeightsStatus,
} from '../../entities/embedding-model'

export const STATUS_TEXT: Readonly<Record<EmbeddingModelStatus, string>> = {
  building: '未完成',
  ready: '可切换',
  current: '当前版本',
  retired: '已下线',
}

export const BUILD_STATE_TEXT: Readonly<Record<BuildState, string>> = {
  running: '正在生成',
  stopping: '正在停止',
  done: '已完成',
  stopped: '已停止',
  failed: '已中止',
}

/** A version's weight files in this image, when that is a problem. */
export const WEIGHTS_TEXT: Readonly<Record<Exclude<WeightsStatus, 'ok'>, string>> = {
  missing: '镜像里没有它的权重',
  changed: '权重文件被改过',
}

const FAILURE_TEXT: Readonly<Record<string, string>> = {
  IMAGE_FILE_UNAVAILABLE: '读不到存储里的原图',
  INDEX_BAD_IMAGE: '原图无法识别为图片',
  INDEX_BAD_EMBEDDING: '生成的向量无效',
}

/** Why one image got no vector, in words. */
export function failureText(code: string): string {
  return FAILURE_TEXT[code] ?? code
}

const REFUSAL_TEXT: Readonly<Record<string, string>> = {
  EMBEDDING_MODEL_EXISTS: '配置的模型已经登记过，或者这个名称和版本已被占用。',
  EMBEDDING_ENCODER_UNAVAILABLE: '这个版本的编码器没有加载，也没能按记录重新加载它的权重。把 EMBEDDING_CONFIG 指向它的权重后再试。',
  EMBEDDING_BUILD_RUNNING: '正在生成向量，等它完成或先停止。',
  EMBEDDING_MODEL_NOT_FOUND: '这个版本已经不存在，请刷新。',
  NOT_READY: '服务未就绪（数据库、向量库或编码器没有启动）。',
}

/** A refused or failed request, in words, with the server's reason and trace id when there is one. */
export function refusalText(cause: unknown): string {
  if (!(cause instanceof ApiError)) return cause instanceof Error ? cause.message : '操作失败，请重试。'
  const known = cause.code ? REFUSAL_TEXT[cause.code] : undefined
  const reason = known ? (cause.detail ? `${known}（${cause.detail}）` : known) : cause.message
  return cause.traceId ? `${reason} 追踪号：${cause.traceId}` : reason
}

/** The weights file's name without folder and extension (what the server names a new version by default). */
export function modelFileStem(path: string | null): string {
  const base = (path ?? '').split(/[\\/]/).pop() ?? ''
  const stem = base.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._-]+|[._-]+$/g, '').slice(0, 64)
  return stem || 'model'
}

export function defaultVersion(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
}

export type GuideTone = 'ok' | 'info' | 'warn' | 'error'
/** The three steps of a model update; done means the configured model is current and complete. */
export type GuideStep = 'register' | 'build' | 'switch' | 'done' | null
export type GuideAction =
  | { kind: 'register' }
  | { kind: 'build'; model: EmbeddingModel }
  | { kind: 'activate'; model: EmbeddingModel }

export interface Guide {
  tone: GuideTone
  title: string
  text: string
  step: GuideStep
  action: GuideAction | null
}

export function modelById(overview: ModelOverview, id: number | null): EmbeddingModel | null {
  return id === null ? null : overview.models.find(model => model.id === id) ?? null
}

/** What the page suggests next, from where the configured model stands. */
export function guideFor(overview: ModelOverview): Guide {
  const { configured, retrieval, images } = overview
  const serving = modelById(overview, retrieval.modelId)
  const target = modelById(overview, configured.modelId)
  const building = buildActive(overview.build)
  const stalled = !retrieval.ready && retrieval.code === 'EMBEDDING_MODEL_MISMATCH'
  const prefix = stalled ? '检索已暂停：配置的模型和当前版本的向量不是同一个模型，不能混用。' : ''
  const tone = (fallback: GuideTone): GuideTone => (stalled ? 'error' : fallback)
  const keeps = serving ? `检索继续用 ${serving.label}，不受影响。` : ''
  if (!configured.loaded) {
    return { tone: 'warn', title: '配置的 embedding 模型没有加载', text: `${configured.detail ?? '原因未知。'} 修好 EMBEDDING_CONFIG 后重启服务。`, step: null, action: null }
  }
  if (!target) {
    return {
      tone: tone('info'),
      title: '配置里换了一个新的 embedding 模型',
      text: `${prefix}EMBEDDING_CONFIG 指向 ${configured.modelPath ?? '—'}，还没有登记。${keeps}登记后它有自己的向量库，再为图库生成向量、切换。`,
      step: 'register',
      action: { kind: 'register' },
    }
  }
  if (target.status === 'current') {
    if (!target.serving) {
      return { tone: tone('warn'), title: '检索没有在用当前版本', text: `${prefix}${retrieval.detail ?? ''} 可以重新启用 ${target.label}。`, step: 'switch', action: { kind: 'activate', model: target } }
    }
    if (lostPoints(target)) {
      return {
        tone: 'warn',
        title: '当前版本有向量丢了',
        text: `写入记录有 ${target.embedded} 张，向量库里实际只有 ${target.points} 个向量。核对后会把丢的重新生成（后台对账也会逐步补上）。`,
        step: 'done',
        action: building ? null : { kind: 'build', model: target },
      }
    }
    if (target.missing > 0) {
      return {
        tone: 'warn',
        title: `当前版本还有 ${target.missing} 张图片没有向量`,
        text: '这些图片检索不到。可以用存储里的原图补齐（只补缺的，已有的不动）。',
        step: 'done',
        action: building ? null : { kind: 'build', model: target },
      }
    }
    return { tone: 'ok', title: '检索在用的就是配置的模型', text: `${target.label} 的向量齐全（${images} 张图片）。`, step: 'done', action: null }
  }
  if (target.missing > 0) {
    return {
      tone: tone('info'),
      title: `为 ${target.label} 生成向量`,
      text: `${prefix}还有 ${target.missing} / ${images} 张图片没有它的向量。${keeps}可以随时停止，再开始会接着做。`,
      step: 'build',
      action: building ? null : { kind: 'build', model: target },
    }
  }
  return {
    tone: tone('info'),
    title: `${target.label} 的向量已经齐全，可以切换`,
    text: `${prefix}切换后检索和入库立即改用它；${serving ? `原来的 ${serving.label} 保留，随时可以切回。` : ''}`,
    step: 'switch',
    action: building ? null : { kind: 'activate', model: target },
  }
}

export interface RowActions {
  build: string | null
  stop: boolean
  activate: string | null
  retire: boolean
}

/** What one version can do now; labels say what will happen. */
export function actionsFor(model: EmbeddingModel, overview: ModelOverview): RowActions {
  const active = buildActive(overview.build)
  const mine = active && overview.build?.modelId === model.id
  const live = model.status !== 'retired'
  const usable = encoderUsable(model)  // in memory, or its weights still in the image
  const lost = lostPoints(model)
  const build = live && usable && (model.missing > 0 || lost) && !active
    ? lost ? '核对并补齐' : model.status === 'current' ? '补齐缺少的向量' : model.embedded > 0 ? '继续生成向量' : '生成向量'
    : null
  let activate: string | null = null
  if (!mine && usable) {
    if (model.status === 'current' && !model.serving) activate = '重新启用检索'
    else if (model.status === 'building' || model.status === 'ready') activate = model.activatedAt ? '切回这个版本' : '切换为当前'
  }
  return { build, stop: mine, activate, retire: (model.status === 'building' || model.status === 'ready') && !mine }
}
