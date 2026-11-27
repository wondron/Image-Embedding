/**
 * How a consistency report reads for the person looking after the library: what each issue code
 * means, why it matters, and where it gets fixed. The backend only sends codes and counts
 * (services/retrieval/consistency.py build_issues); the words are here.
 */
import { ApiError } from '../../shared/api'
import type { CheckState, EncoderCheckStatus, FileFindingKey, Issue, IssueLevel, VersionFindingKey, VersionReport } from '../../entities/consistency'

/** Pages an issue can send the person to. */
export type FixView = 'models' | 'scenes' | 'tasks' | 'library'
export interface FixLink { view: FixView; label: string; params?: Readonly<Record<string, string>> }

export interface IssueText {
  title: string
  text: string
  fix?: FixLink
}

export const LEVEL_TEXT: Readonly<Record<IssueLevel, string>> = { error: '不一致', warn: '需要处理', info: '说明' }

export const STATE_TEXT: Readonly<Record<CheckState, string>> = {
  running: '正在核对',
  stopping: '正在停止',
  done: '已完成',
  stopped: '已停止（只核对了一部分）',
  failed: '核对失败',
}

export const PHASE_TEXT: Readonly<Record<string, string>> = {
  starting: '准备',
  collections: '读取各版本的 collection',
  images: '逐张核对图片（向量和文件）',
  points: '翻查向量库里多余的点',
  files: '列出存储里的文件',
  recheck: '复查核对期间新入库的',
  encoders: '用编码器抽查向量',
}

export const ENCODER_TEXT: Readonly<Record<EncoderCheckStatus, string>> = {
  ok: '向量是它的编码器写的',
  mismatch: '向量不是它的编码器写的',
  unchecked: '没能抽查',
  not_loaded: '编码器没加载，跳过',
  off: '没有抽查',
}

export const VERSION_FINDING_TEXT: Readonly<Record<VersionFindingKey, string>> = {
  missing: '没有向量',
  lost: '向量丢了',
  unrecorded: '没有写入记录',
  ledger_behind: '过滤信息待补推',
  stale_payload: '过滤信息不一致',
  orphan_points: '多余的点',
  pending_points: '等着删除的点',
}

export const FILE_FINDING_TEXT: Readonly<Record<FileFindingKey, string>> = {
  missing_original: '原图不在',
  missing_thumb: '缩略图不在',
  unreadable: '查不了',
  content_mismatch: '原图内容不对',
  orphan_files: '多余的文件',
  pending_files: '等着删除的文件',
}

/** The backend's fixed reasons (services/retrieval/consistency.py, bootstrap/consistency.py) in words; errors pass through. */
const DETAIL_TEXT: Readonly<Record<string, string>> = {
  'no stored image could be embedded again': '存储里这几张的原图都读不到，没能重算',
  'no vectors yet': '这个版本还没有向量',
  'collection is not readable': '向量库读不了',
  'object storage is not available': '对象存储不可用',
  'Qdrant is not available': 'Qdrant 不可用',
  'retrieval is not running': '检索服务没有运行',
}

export function detailText(detail: string | null): string | null {
  if (!detail) return detail
  if (DETAIL_TEXT[detail]) return DETAIL_TEXT[detail]
  if (detail.startsWith('mock storage keeps the bytes in memory only')) return 'mock 存储只把文件放在内存里，配置 IMAGE_STORAGE_CONFIG.mock_mirror_dir 才能核对文件。'
  if (detail.startsWith('this storage driver cannot')) return '这种存储驱动查不了文件。'
  return detail
}

/** Findings that are a problem by themselves (the others are taken care of automatically). */
export const VERSION_PROBLEMS: readonly VersionFindingKey[] = ['missing', 'lost', 'unrecorded', 'stale_payload', 'orphan_points']

const MODELS: FixLink = { view: 'models', label: '去「模型版本」' }
const SCENES_SYNC: FixLink = { view: 'scenes', label: '去「场景管理」全量同步', params: { sceneTab: 'sync' } }
const DELETIONS: FixLink = { view: 'tasks', label: '去「入库任务」看待清理图片' }

/** The issue in words; `version` is the report row the issue is about (its status changes the advice). */
export function issueText(issue: Issue, version?: VersionReport | null): IssueText {
  const n = issue.count ?? 0
  const who = issue.label ?? (version ? version.label : '')
  const current = version?.status === 'current'
  const building = version?.status === 'building'
  switch (issue.code) {
    case 'postgres_unreachable':
      return { title: 'PostgreSQL 连不上', text: `本进程读不了数据库，什么都核对不了。${issue.detail ?? ''}` }
    case 'qdrant_unreachable':
      return { title: 'Qdrant 连不上', text: `向量库这边都没有核对。检查 QDRANT_CONFIG 和网络。${issue.detail ? `（${issue.detail}）` : ''}` }
    case 'storage_unreachable':
      return { title: '对象存储连不上', text: `检查 IMAGE_STORAGE_CONFIG 的 endpoint、bucket 和密钥。${issue.detail ? `（${issue.detail}）` : ''}` }
    case 'qdrant_restart_required':
      return {
        title: 'Qdrant 地址改了，本进程还没重启',
        text: `配置里（含 Nacos）的地址现在是 ${issue.detail ?? '另一个'}，本进程还连着启动时的那个：检索、入库和后台对账都还在用旧的库。重启服务后再核对一次。`,
      }
    case 'no_current_model':
      return { title: '没有当前模型版本', text: 'embedding_models 里没有 status=current 的版本，检索起不来。', fix: MODELS }
    case 'retrieval_not_ready':
      return { title: '本进程的检索没在运行', text: `所以看不出检索在用哪个版本。${issue.detail ? `原因：${detailText(issue.detail)}` : ''}`, fix: MODELS }
    case 'serving_not_current':
      return {
        title: '本进程检索用的不是当前版本',
        text: `数据库里的当前版本换了（多半是另一台服务器切换的），本进程还在用 ${issue.detail ?? '旧版本'}。后台对账下一轮会跟着切换；不想等可以重启。`,
        fix: MODELS,
      }
    case 'deletion_queue':
      return issue.level === 'warn'
        ? { title: `${n} 张已删除的图片等着清理，最早的超过 1 小时`, text: '它们的向量和文件还没删。多半是后台对账没开（RECONCILE_CONFIG.enabled）或一直清理失败。', fix: DELETIONS }
        : { title: `${n} 张已删除的图片等着清理`, text: '后台对账会删掉它们的向量和文件。', fix: DELETIONS }
    case 'version_unreadable':
      return { title: `${who} 的向量库读不了`, text: `${detailText(issue.detail) ?? ''}；这个版本后面的核对都跳过了。` }
    case 'collection_missing':
      return {
        title: `${who} 的 collection 不在 Qdrant 里`,
        text: '数据库登记了这个版本，这个 Qdrant 上却没有它的 collection：被手工删了，或者本进程连的不是写它的那个 Qdrant（先看上面的连接）。生成向量时会重新建并补齐。',
        fix: MODELS,
      }
    case 'collection_mismatch':
      return { title: `${who} 的 collection 结构和登记的不一样`, text: `实际是 ${issue.detail ?? '?'}。检索会出错，不要再往这个库写入。` }
    case 'missing_vectors':
      return {
        title: `${who}：${n} 张图片没有向量`,
        text: current
          ? '检索找不到它们。后台对账每轮会为当前版本补一部分（RECONCILE_CONFIG.max_embeddings）；也可以到「模型版本」点「补齐缺少的向量」。'
          : building ? '这个版本还没生成完，到「模型版本」继续生成。' : '切换到这个版本之前先为它「生成向量」，否则切换后检索不到它们。',
        fix: MODELS,
      }
    case 'lost_vectors':
      return {
        title: `${who}：${n} 张的向量丢了`,
        text: '写入记录说有，collection 里却没有这个点：collection 被重建、点被删了，或者两台服务器连着不同的 Qdrant。对账和生成向量会先核对写入记录，再重新生成。',
        fix: MODELS,
      }
    case 'unrecorded_vectors':
      return {
        title: `${who}：${n} 个点没有写入记录`,
        text: '点在，image_embeddings 里却没有记录：可能是连着同一个 Qdrant、用另一个数据库的服务器写的，或者入库时记录没写成。补向量时会重写这些点并记上。',
        fix: MODELS,
      }
    case 'stale_payloads':
      return {
        title: `${who}：${n} 张的过滤信息和数据库不一致`,
        text: `写入记录说是最新的，向量库里的场景、类别或标注状态却不一样：按场景搜索会漏掉或多出这些图。不会自动补推，需要全量同步。${current ? '' : '（全量同步只写当前版本：这个版本切换过去之后再同步一次。）'}`,
        fix: current ? SCENES_SYNC : undefined,
      }
    case 'ledger_behind':
      return {
        title: `${who}：${n} 张的过滤信息待补推`,
        text: current ? '数据库改过、还没推到向量库，后台对账会补推。' : '这个版本在生成向量时会一起补推。',
      }
    case 'orphan_points':
      return {
        title: `${who}：${n} 个多余的点`,
        text: '向量库里有、数据库里没有（也不在删除队列里）的点：多半是另一个环境写进来的，或删除清理失败留下的。检索时它们会占掉结果名额，返回的结果可能比要的少。先确认没有别的服务在用这个 collection，再处理。',
      }
    case 'pending_points':
      return { title: `${who}：${n} 个点等着删除`, text: '图片已经删了，后台对账会删掉这些点。' }
    case 'versions_differ':
      return {
        title: `${who} 和当前版本有 ${n} 张图不一样`,
        text: building ? '只有一边有向量的图片；这个版本还在生成，生成完就会补齐。' : '只有一边有向量的图片。切换到它之前先「生成向量」补齐，否则切换后这些图检索不到。',
        fix: MODELS,
      }
    case 'encoder_mismatch':
      return {
        title: `${who} 的向量不是它的编码器写的`,
        text: `用这个版本的编码器重算了 ${n} 张已存图片，和 collection 里的向量最低相似度只有 ${issue.detail ?? '?'}：这个 collection 混进了别的模型的向量，不能用来检索。重新登记一个版本并生成向量。`,
        fix: MODELS,
      }
    case 'weights_unavailable':
      return {
        title: `${who} 的权重${issue.detail === 'changed' ? '文件被改过' : '不在镜像里'}`,
        text: '切换或回滚到它时，要从镜像里加载它自己的权重。',
        fix: MODELS,
      }
    case 'retired_collection_left':
      return { title: `${n} 个已下线版本的 collection 还在`, text: `${issue.detail ?? ''}：下线时应该已经删掉。确认没用后可以在 Qdrant 里删除。` }
    case 'stray_collections':
      return { title: `这个 Qdrant 上还有 ${n} 个别的 collection`, text: `${issue.detail ?? ''}：不是本服务登记的（可能属于别的程序），不影响检索。` }
    case 'collections_unlisted':
      return { title: '列不出 Qdrant 上的 collection', text: issue.detail ?? '' }
    case 'files_unchecked':
      return { title: '文件没有核对', text: detailText(issue.detail) ?? '' }
    case 'missing_original':
      return {
        title: `${n} 张图片的原图不在存储里`,
        text: '这些图生成不了新版本的向量，详情里也看不到原图。同一个文件重新上传会写回原位置；更早入库、存在不分片目录或别的 key_prefix 下的，要从备份恢复到它的 URL 所指的位置。',
      }
    case 'missing_thumb':
      return { title: `${n} 张图片的缩略图不在存储里`, text: '页面会退回加载原图，慢一些。' }
    case 'files_unreadable':
      return {
        title: `${n} 张图片的文件查不了`,
        text: `没有权限或网络出错，说不清文件在不在。检查对象存储密钥的权限（HeadObject、GetObject）。${issue.detail ? `第一个错误：${issue.detail}` : ''}`,
      }
    case 'content_mismatch':
      return { title: `${n} 张原图的内容不对`, text: '下载下来的原图和入库时记录的 SHA-256 不一样：文件被覆盖或损坏了。' }
    case 'orphan_files':
      return { title: `存储里有 ${n} 个多余的文件`, text: '数据库里没有引用的原图或缩略图：多半是入库中途失败留下的，或者别的环境写在同一个前缀下。只占空间，不影响检索。' }
    case 'pending_files':
      return { title: `${n} 个文件等着删除`, text: '图片已经删了，后台对账会删掉这些文件。', fix: DELETIONS }
    case 'file_list_failed':
      return { title: '列不出存储里的文件', text: `多余文件没有核对（需要 ListBucket 权限）。${issue.detail ?? ''}` }
    case 'file_list_truncated':
      return { title: `存储里的文件太多，只列了前 ${n} 个`, text: '多余文件的结果不完整。' }
    case 'check_incomplete':
      return { title: '核对中途停了', text: '结果只覆盖已经核对的部分。' }
    default:
      return { title: issue.code, text: issue.detail ?? '' }
  }
}

const REFUSAL_TEXT: Readonly<Record<string, string>> = {
  CONSISTENCY_CHECK_RUNNING: '已经在核对了，等它完成或先停止。',
  NOT_READY: '服务未就绪：数据库还没连上。',
  VALIDATION_ERROR: '核对选项不正确。',
}

/** A refused or failed request, in words, with the server's reason and trace id when there is one. */
export function refusalText(cause: unknown): string {
  if (!(cause instanceof ApiError)) return cause instanceof Error ? cause.message : '操作失败，请重试。'
  const known = cause.code ? REFUSAL_TEXT[cause.code] : undefined
  const reason = known ? (cause.detail ? `${known}（${cause.detail}）` : known) : cause.message
  return cause.traceId ? `${reason} 追踪号：${cause.traceId}` : reason
}

/** 「不到 1 秒」 / 「12.3 秒」 / 「2 分 05 秒」. */
export function durationText(ms: number): string {
  if (ms < 1000) return '不到 1 秒'
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} 分 ${String(Math.round(seconds - minutes * 60)).padStart(2, '0')} 秒`
}

/** Progress in words: 「逐张核对图片（向量和文件）· 1500 / 15231」. */
export function progressText(phase: string, done: number, total: number | null, label: string | null): string {
  const name = PHASE_TEXT[phase] ?? phase
  const of = total !== null && total > 0 ? `${done} / ${total}` : done > 0 ? String(done) : ''
  return [label ? `${name}（${label}）` : name, of].filter(Boolean).join(' · ')
}
