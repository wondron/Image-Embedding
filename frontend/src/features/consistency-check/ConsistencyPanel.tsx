import { useState } from 'react'
import {
  ArrowRight, CircleAlert, CircleCheck, Copy, Database, FolderTree, HardDrive, Info, LoaderCircle, Play,
  RefreshCw, ScanSearch, Server, Square, TriangleAlert,
} from 'lucide-react'
import {
  DEFAULT_CHECK_OPTIONS,
  FILE_FINDINGS,
  VERSION_FINDINGS,
  type CheckOptions,
  type ConsistencyReport,
  type ConsistencyRun,
  type Finding,
  type Issue,
  type VersionReport,
} from '../../entities/consistency'
import { formatLibraryTime } from '../../shared/lib'
import { EmptyState, notify } from '../../shared/ui'
import {
  ENCODER_TEXT, FILE_FINDING_TEXT, LEVEL_TEXT, STATE_TEXT, VERSION_FINDING_TEXT, VERSION_PROBLEMS,
  detailText, durationText, issueText, progressText, refusalText, type FixView,
} from './messages'
import { useConsistencyCheck } from './useConsistencyCheck'
import './consistency-check.css'

type OnOpen = (view: FixView, params?: Readonly<Record<string, string>>) => void

const OPTION_ROWS: readonly { key: keyof CheckOptions; label: string; hint: string }[] = [
  { key: 'vectors', label: 'PostgreSQL ↔ Qdrant', hint: '每个在用版本：有没有向量、写入记录、过滤信息（payload）' },
  { key: 'files', label: 'PostgreSQL → 对象存储', hint: '每张图的原图和缩略图还在不在（只发 HEAD，不下载）' },
  { key: 'orphans', label: '找多余的点和文件', hint: '向量库、存储 original/ thumb/ 下有，数据库里没有的' },
  { key: 'encoders', label: '抽查编码器', hint: '用已加载的编码器重算几张图，确认向量是这个版本写的' },
  { key: 'content', label: '下载原图核对内容', hint: '每张原图下载一次，比对 SHA-256；图多时很慢' },
]

const STATUS_TEXT: Readonly<Record<string, string>> = { current: '当前版本', ready: '可切换', building: '未完成' }

/** The finding(s) an issue lists, to show its first ids. */
function samplesOf(issue: Issue, report: ConsistencyReport): Finding[] {
  const version = report.versions.find(v => v.id === issue.versionId)
  const fromVersion: Readonly<Record<string, keyof VersionReport['findings']>> = {
    missing_vectors: 'missing', lost_vectors: 'lost', unrecorded_vectors: 'unrecorded', stale_payloads: 'stale_payload',
    ledger_behind: 'ledger_behind', orphan_points: 'orphan_points', pending_points: 'pending_points',
  }
  if (version && fromVersion[issue.code]) return [version.findings[fromVersion[issue.code]]]
  if (version && issue.code === 'versions_differ' && version.vsCurrent) return [version.vsCurrent.onlyCurrent, version.vsCurrent.onlyThis]
  const files = report.files?.findings
  if (!files) return []
  const fromFiles: Readonly<Record<string, keyof typeof files>> = {
    missing_original: 'missing_original', missing_thumb: 'missing_thumb', files_unreadable: 'unreadable',
    content_mismatch: 'content_mismatch', orphan_files: 'orphan_files', pending_files: 'pending_files',
  }
  return fromFiles[issue.code] ? [files[fromFiles[issue.code]]] : []
}

async function copyLines(lines: string[]) {
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    notify.success(`已复制 ${lines.length} 条。`)
  } catch {
    notify.warn('浏览器不让复制，请手动选中文字复制。')
  }
}

function Samples({ findings }: { findings: Finding[] }) {
  const samples = [...new Set(findings.flatMap(f => f.samples))]
  const total = findings.reduce((sum, f) => sum + f.count, 0)
  if (!samples.length) return null
  return <details className="cc-samples">
    <summary>{total > samples.length ? `列出前 ${samples.length} 个（共 ${total}）` : `列出 ${samples.length} 个`}</summary>
    <ul>{samples.map(item => <li key={item}><code>{item}</code></li>)}</ul>
    <button type="button" className="mg-btn mg-secondary" onClick={() => void copyLines(samples)}><Copy size={13} />复制</button>
  </details>
}

function IssueRow({ issue, report, onOpen }: { issue: Issue; report: ConsistencyReport; onOpen?: OnOpen }) {
  const version = report.versions.find(v => v.id === issue.versionId) ?? null
  const text = issueText(issue, version)
  const Icon = issue.level === 'error' ? CircleAlert : issue.level === 'warn' ? TriangleAlert : Info
  return <li className={`cc-issue is-${issue.level}`}>
    <Icon size={17} aria-hidden />
    <div>
      <div className="cc-issue-head">
        <strong>{text.title}</strong>
        <span className={`cc-level is-${issue.level}`}>{LEVEL_TEXT[issue.level]}</span>
      </div>
      <p>{text.text}</p>
      <Samples findings={samplesOf(issue, report)} />
    </div>
    {text.fix && onOpen && <button type="button" className="mg-btn mg-secondary" onClick={() => onOpen(text.fix!.view, text.fix!.params)}>
      {text.fix.label}<ArrowRight size={14} />
    </button>}
  </li>
}

function Connection({ icon: Icon, title, ok, lines, warning }: {
  icon: typeof Database
  title: string
  ok: boolean
  lines: (string | null)[]
  warning?: string | null
}) {
  return <div className={`cc-connection ${ok ? 'is-ok' : 'is-error'}`}>
    <div className="cc-connection-head"><Icon size={16} aria-hidden /><strong>{title}</strong><span>{ok ? '已连接' : '不可用'}</span></div>
    {lines.filter((line): line is string => Boolean(line)).map(line => <small key={line}>{line}</small>)}
    {warning && <small className="is-warn">{warning}</small>}
  </div>
}

function Connections({ report }: { report: ConsistencyReport }) {
  const { postgres, qdrant, storage, serving } = report.connections
  return <section className="cc-connections" aria-label="本进程连着的服务">
    <Connection icon={Database} title="PostgreSQL" ok={postgres.ok}
      lines={[postgres.host && `${postgres.host} / ${postgres.database ?? '?'}`, `${report.database.images} 张图片`, postgres.detail]} />
    <Connection icon={Server} title="Qdrant" ok={qdrant.ok}
      lines={[qdrant.url, qdrant.collections !== null ? `${qdrant.collections} 个 collection` : null, qdrant.detail]}
      warning={qdrant.restartRequired ? `配置里现在是 ${qdrant.configuredUrl}，要重启才会改连` : null} />
    <Connection icon={HardDrive} title="对象存储" ok={storage.ok}
      lines={[storage.driver && `${storage.driver} · ${storage.bucket ?? '?'}`, storage.endpointUrl ?? storage.publicBaseUrl,
        storage.keyPrefix ? `key_prefix ${storage.keyPrefix}` : null, storage.detail]} />
    <Connection icon={ScanSearch} title="检索在用" ok={serving.modelId !== null}
      lines={[serving.label, detailText(serving.detail)]} />
  </section>
}

function Count({ value, warn }: { value: number; warn?: boolean }) {
  return <td className={value > 0 && warn ? 'is-warn' : value > 0 ? 'is-some' : 'is-zero'}>{value}</td>
}

function VersionsTable({ report }: { report: ConsistencyReport }) {
  if (!report.options.vectors && !report.options.encoders) return null
  if (!report.versions.length) return <EmptyState icon={FolderTree} title="没有在用的模型版本" description="embedding_models 里没有 building / ready / current 的版本。" />
  return <section className="mg-panel cc-versions" aria-label="各版本的向量">
    <h3>各版本的向量库</h3>
    <div className="cc-table-scroll"><table className="cc-table">
      <thead><tr>
        <th scope="col">版本</th><th scope="col">点 / 有向量 / 写入记录</th>
        {VERSION_FINDINGS.map(key => <th key={key} scope="col">{VERSION_FINDING_TEXT[key]}</th>)}
        <th scope="col">和当前版本不同</th><th scope="col">编码器抽查</th>
      </tr></thead>
      <tbody>{report.versions.map(version => {
        const differ = version.vsCurrent ? version.vsCurrent.onlyCurrent.count + version.vsCurrent.onlyThis.count : null
        const q = version.qdrant
        return <tr key={version.id}>
          <th scope="row">
            <strong>{version.label}</strong>
            <span className={`model-status is-${version.status}`}>{STATUS_TEXT[version.status] ?? version.status}</span>
            {version.serving && <span className="model-tag is-serving">检索在用</span>}
            <small><code>{version.collection}</code>{q.exists === false ? ' · 不在 Qdrant 里' : q.matches === false ? ` · 实际 ${q.dim} 维 / ${q.distance}` : ''}</small>
            {version.error && <small className="is-error">{detailText(version.error)}</small>}
          </th>
          <td>{q.points ?? '—'} / {version.withVector} / {version.ledger}<small>核对 {version.scanned} 张</small></td>
          {VERSION_FINDINGS.map(key => <Count key={key} value={version.findings[key].count} warn={VERSION_PROBLEMS.includes(key) && version.status !== 'building'} />)}
          {differ === null ? <td className="is-zero">—</td> : <Count value={differ} warn={version.status === 'ready'} />}
          <td className={`cc-encoder is-${version.encoder.status}`}>
            {ENCODER_TEXT[version.encoder.status]}
            {version.encoder.checked > 0 && <small>{version.encoder.checked} 张，最低 {version.encoder.minSimilarity?.toFixed(4)}</small>}
            {version.encoder.detail && <small>{detailText(version.encoder.detail)}</small>}
          </td>
        </tr>
      })}</tbody>
    </table></div>
  </section>
}

function FilesSection({ report }: { report: ConsistencyReport }) {
  const files = report.files
  if (!files) return null
  return <section className="mg-panel cc-files" aria-label="对象存储里的文件">
    <h3>对象存储里的文件</h3>
    {files.skipped ? <p className="cc-note">没有核对：{detailText(files.skipped)}</p> : <>
      <dl className="cc-counts">
        <div><dt>核对的图片</dt><dd>{files.checked}</dd></div>
        {FILE_FINDINGS.map(key => <div key={key} className={files.findings[key].count ? 'is-warn' : ''}>
          <dt>{FILE_FINDING_TEXT[key]}</dt><dd>{files.findings[key].count}</dd>
        </div>)}
        {files.listed !== null && <div><dt>列出的对象</dt><dd>{files.listed}</dd></div>}
      </dl>
      {!report.options.content && <p className="cc-note">没有下载原图核对内容（勾选「下载原图核对内容」才会做）。</p>}
    </>}
  </section>
}

function StrayCollections({ report }: { report: ConsistencyReport }) {
  const stray = report.strayCollections
  if (!stray?.length) return null
  return <details className="mg-panel cc-stray">
    <summary>这个 Qdrant 上其他的 collection（{stray.length}）</summary>
    <ul>{stray.map(item => <li key={item.name}><code>{item.name}</code>{item.label ? ` · 已下线的 ${item.label}` : ' · 不是本服务登记的'}</li>)}</ul>
  </details>
}

function Summary({ run, report }: { run: ConsistencyRun; report: ConsistencyReport }) {
  const problems = report.issues.filter(issue => issue.level !== 'info').length
  const Icon = report.status === 'ok' ? CircleCheck : report.status === 'error' ? CircleAlert : TriangleAlert
  const title = report.status === 'ok'
    ? (report.complete ? 'PostgreSQL、Qdrant 和对象存储一致' : '已核对的部分没有发现问题')
    : `有 ${problems} 项${report.status === 'error' ? '不一致或查不了' : '需要处理'}`
  return <div className={`model-guide is-${report.status}`} role="status">
    <Icon size={18} aria-hidden />
    <div>
      <strong>{title}</strong>
      <p>
        {formatLibraryTime(run.startedAt)} 开始{run.finishedAt ? `，用时 ${durationText(report.durationMs)}` : ''} ·
        核对了 {report.database.images} 张图片、{report.versions.length} 个在用版本
        {!report.complete && ' · 中途停了，只核对了一部分'}
      </p>
    </div>
  </div>
}

/**
 * 数据核对: whether PostgreSQL, Qdrant and the object storage agree, whether the stored files are
 * still there, and whether the live model versions agree with each other. Read only: it reports,
 * and each issue says where it gets fixed.
 */
export function ConsistencyPanel({ apiBase, onOpen }: { apiBase: string; onOpen?: OnOpen }) {
  const check = useConsistencyCheck(apiBase, run => {
    if (run.state === 'failed') notify.error(run.error ?? '原因未知', { title: '数据核对失败' })
    else if (run.report?.status === 'ok') notify.success(run.state === 'stopped' ? '已停止；已核对的部分没有问题。' : '核对完成，没有发现不一致。')
    else if (run.report) notify.warn(`核对${run.state === 'stopped' ? '已停止' : '完成'}，有 ${run.report.issues.filter(i => i.level !== 'info').length} 项要看。`, { title: '数据核对' })
  })
  const [options, setOptions] = useState<CheckOptions>(DEFAULT_CHECK_OPTIONS)
  const [working, setWorking] = useState(false)
  const { run, loading, error, active } = check
  const busy = working || active
  const nothingChosen = !options.vectors && !options.files && !options.encoders

  async function act(action: () => Promise<void>, title: string) {
    setWorking(true)
    try {
      await action()
    } catch (cause) {
      notify.error(refusalText(cause), { title })
      void check.refresh()
    } finally {
      setWorking(false)
    }
  }

  function toggle(key: keyof CheckOptions, value: boolean) {
    setOptions(current => {
      const next = { ...current, [key]: value }
      if (key === 'content' && value) next.files = true
      if (key === 'files' && !value) next.content = false
      return next
    })
  }

  const report = run?.report ?? null
  return <section className="consistency-check" aria-label="数据核对" aria-busy={loading || busy}>
    <section className="mg-panel cc-start" aria-label="核对选项">
      <div className="cc-start-text">
        <strong>核对本进程连着的 PostgreSQL、Qdrant 和对象存储</strong>
        <p>只读，不修改任何数据；每一项问题都写了去哪里修。核对在服务端后台进行，可以离开这个页面。两台服务器共用一个数据库时，在两边各核对一次，对比它们连的 Qdrant。</p>
      </div>
      <fieldset className="cc-options" disabled={busy}>
        <legend>核对哪些</legend>
        {OPTION_ROWS.map(row => <label key={row.key} title={row.hint}>
          <input type="checkbox" checked={options[row.key]} disabled={row.key === 'orphans' && !options.vectors && !options.files}
            onChange={event => toggle(row.key, event.target.checked)} />
          <span>{row.label}<small>{row.hint}</small></span>
        </label>)}
      </fieldset>
      <div className="cc-actions">
        {active
          ? <button type="button" className="mg-btn mg-secondary" disabled={working || run?.state === 'stopping'} onClick={() => void act(check.stop, '没有停止')}><Square size={14} />停止</button>
          : <button type="button" className="mg-btn mg-primary" disabled={busy || nothingChosen || loading && run === undefined} onClick={() => void act(() => check.start(options), '没有开始核对')}><Play size={14} />开始核对</button>}
        <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={() => void check.refresh()}><RefreshCw size={14} />刷新</button>
        {error && <span className="mg-form-error" role="alert">读取失败{run !== undefined ? '，显示上次读取的结果' : ''}。{refusalText(error)}</span>}
      </div>
    </section>

    {run && active && <section className="cc-progress" aria-live="polite" aria-label="核对进度">
      <div className="cc-progress-head"><LoaderCircle size={15} className="spin" aria-hidden /><strong>{STATE_TEXT[run.state]}</strong>
        <span>{progressText(run.progress.phase, run.progress.done, run.progress.total, run.progress.label)}</span></div>
      {run.progress.total ? <div className="model-coverage-bar" role="progressbar" aria-valuemin={0} aria-valuemax={run.progress.total} aria-valuenow={run.progress.done}>
        <span style={{ width: `${Math.min(100, (run.progress.done / run.progress.total) * 100)}%` }} />
      </div> : null}
      <small>开始于 {formatLibraryTime(run.startedAt)}。检索和入库照常进行；核对期间写入的数据，个别条目可能显示成不一致，再核对一次即可确认。</small>
    </section>}

    {run?.state === 'failed' && <div className="model-guide is-error" role="alert"><CircleAlert size={18} aria-hidden /><div>
      <strong>核对失败</strong><p>{run.error ?? '原因未知'}</p></div></div>}

    {run === null && !loading && <EmptyState icon={ScanSearch} title="还没有核对过" description="本进程启动以来还没有核对。选好要核对的内容，点「开始核对」。结果只保存在服务端内存里，重启后清空。" />}

    {run && report && <>
      <Summary run={run} report={report} />
      <Connections report={report} />
      {report.issues.length > 0 && <section className="mg-panel cc-issues" aria-label="要处理的事">
        <h3>要处理的事</h3>
        <ul>{report.issues.map((issue, index) => <IssueRow key={`${issue.code}-${issue.versionId ?? ''}-${index}`} issue={issue} report={report} onOpen={onOpen} />)}</ul>
      </section>}
      <VersionsTable report={report} />
      <FilesSection report={report} />
      <StrayCollections report={report} />
    </>}

    <details className="model-help">
      <summary>核对了什么、怎么修</summary>
      <ul>
        <li>PostgreSQL 是权威：图片、写入记录（image_embeddings）和每张图该有的过滤信息都以它为准。每个在用的模型版本（当前、可切换、未完成）都有自己的 collection，逐个和数据库对比。</li>
        <li>「没有向量」：没生成过；「向量丢了」：写入记录说有、点却不在；「没有写入记录」：点在、记录没有。当前版本由后台对账逐步补上，其他版本到「模型版本」生成向量。</li>
        <li>「过滤信息待补推」会自动补；「过滤信息不一致」是记录说最新、实际不是，要到「场景管理」做一次全量同步。</li>
        <li>「多余的点」会占掉检索的结果名额；「多余的文件」只占空间。两者都可能是别的环境写进来的，确认后再删。</li>
        <li>「和当前版本不同」：只有一边有向量的图片；切换到那个版本前先补齐。「编码器抽查」用这个版本的编码器重算几张已存图片，向量对不上说明 collection 里混进了别的模型的向量。</li>
        <li>连接一栏显示的是本进程实际连着的地址。Qdrant 地址只在启动时读取：Nacos 里改了地址、没重启时会标出来。</li>
        <li>核对期间有入库：入库先写文件、再写数据库和向量库，所以核对最后会复查看起来多余的点和文件，期间提交了的不算。</li>
      </ul>
    </details>
  </section>
}
