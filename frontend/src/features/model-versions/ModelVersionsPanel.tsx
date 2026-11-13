import { useState } from 'react'
import { ArrowRightLeft, CircleAlert, CircleCheck, Cpu, Images, Info, LoaderCircle, PackagePlus, Play, RefreshCw, ScanSearch, Square, Trash2, TriangleAlert } from 'lucide-react'
import {
  activateEmbeddingModel,
  buildActive,
  lostPoints,
  registerEmbeddingModel,
  shortArtifact,
  retireEmbeddingModel,
  startModelBuild,
  stopModelBuild,
  type EmbeddingModel,
  type ModelBuild,
  type ModelOverview,
} from '../../entities/embedding-model'
import { formatLibraryTime } from '../../shared/lib'
import { EmptyState, Metric, notify } from '../../shared/ui'
import { ActivateModelDialog, RegisterModelDialog, RetireModelDialog } from './ModelDialogs'
import { BUILD_STATE_TEXT, STATUS_TEXT, WEIGHTS_TEXT, actionsFor, failureText, guideFor, modelById, refusalText, type GuideStep } from './messages'
import { useModelVersions } from './useModelVersions'
import './model-versions.css'

type Pending = { kind: 'register' } | { kind: 'activate'; model: EmbeddingModel } | { kind: 'retire'; model: EmbeddingModel } | null

const STEPS: readonly { key: Exclude<GuideStep, 'done' | null>; label: string }[] = [
  { key: 'register', label: '登记新版本' },
  { key: 'build', label: '生成向量' },
  { key: 'switch', label: '切换' },
]

function Steps({ step }: { step: GuideStep }) {
  if (step === null) return null
  const at = step === 'done' ? STEPS.length : STEPS.findIndex(entry => entry.key === step)
  return <ol className="model-steps" aria-label="换模型的步骤">
    {STEPS.map((entry, index) => <li key={entry.key} className={index < at ? 'is-done' : index === at ? 'is-current' : ''} aria-current={index === at ? 'step' : undefined}>
      <span>{index < at ? <CircleCheck size={14} aria-hidden /> : index + 1}</span>{entry.label}
    </li>)}
  </ol>
}

function Coverage({ model, images }: { model: EmbeddingModel; images: number }) {
  const percent = images > 0 ? Math.round((model.embedded / images) * 1000) / 10 : 100
  return <div className="model-coverage">
    <div className="model-coverage-bar" role="progressbar" aria-label={`${model.label} 有向量的图片`} aria-valuemin={0} aria-valuemax={images} aria-valuenow={model.embedded}>
      <span style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
    <small>{model.embedded} / {images} 张有向量{model.missing > 0 && <>，<strong>{model.missing}</strong> 张没有</>}</small>
  </div>
}

function BuildProgress({ build, model, images, onStop, stopping }: {
  build: ModelBuild
  model: EmbeddingModel | null
  images: number
  onStop: () => void
  stopping: boolean
}) {
  const active = buildActive(build)
  return <section className={`model-build is-${build.state}`} aria-label="生成向量进度" aria-live="polite">
    <div className="model-build-head">
      <strong>{active ? <><LoaderCircle size={15} className="spin" aria-hidden />正在为 </> : '最近一次生成向量：'}{model?.label ?? `版本 ${build.modelId}`}</strong>
      <span className={`model-build-state is-${build.state}`}>{BUILD_STATE_TEXT[build.state]}</span>
      {active && <button type="button" className="mg-btn mg-secondary" disabled={stopping || build.state === 'stopping'} onClick={onStop}><Square size={14} />停止</button>}
    </div>
    {model && <Coverage model={model} images={images} />}
    <dl className="model-build-counts">
      <div className={build.repaired ? 'is-warn' : ''}><dt>核对写入记录</dt><dd>{build.verified}</dd>{build.repaired > 0 && <small>{build.repaired} 张的向量已丢失，重新生成</small>}</div>
      <div><dt>本次写入</dt><dd>{build.embedded}</dd></div>
      <div className={build.failed ? 'is-warn' : ''}><dt>没生成</dt><dd>{build.failed}</dd></div>
      <div><dt>处理时已删除</dt><dd>{build.gone}</dd></div>
      <div><dt>补推过滤信息</dt><dd>{build.payloads}</dd></div>
    </dl>
    <p className="model-build-note">
      开始于 {formatLibraryTime(build.startedAt)}{build.finishedAt && `，结束于 ${formatLibraryTime(build.finishedAt)}`}。
      {active && '检索继续用当前版本；可以离开这个页面，生成在服务端进行。'}
      {build.state === 'stopped' && '已写入的向量保留，再点「继续生成向量」会接着做。'}
      {build.state === 'done' && build.remaining === 0 && '图库里的图片都有这个版本的向量了。'}
      {build.state === 'done' && (build.remaining ?? 0) > 0 && `还有 ${build.remaining} 张没有向量（多半是原图读不到，见下面的列表）。`}
    </p>
    {build.error && <p className="mg-form-error" role="alert">生成中止：{build.error}</p>}
    {build.failures.length > 0 && <details className="model-build-failures">
      <summary>没生成向量的图片（{build.failed}{build.failed > build.failures.length ? `，列出前 ${build.failures.length} 张` : ''}）</summary>
      <ul>{build.failures.map(failure => <li key={failure.imageId}><code>{failure.imageId}</code>{failureText(failure.code)}</li>)}</ul>
      <p>原图读不到的图片，需要重新上传同一张图片；其余的可以再生成一次。</p>
    </details>}
  </section>
}

function ModelRow({ model, overview, busy, onBuild, onStop, onActivate, onRetire }: {
  model: EmbeddingModel
  overview: ModelOverview
  busy: boolean
  onBuild: () => void
  onStop: () => void
  onActivate: () => void
  onRetire: () => void
}) {
  const actions = actionsFor(model, overview)
  const retired = model.status === 'retired'
  return <article className={`model-row is-${model.status}`} role="listitem" aria-label={model.label}>
    <div className="model-row-main">
      <div className="model-row-title">
        <strong>{model.label}</strong>
        <span className={`model-status is-${model.status}`}>{STATUS_TEXT[model.status]}</span>
        {model.serving && <span className="model-tag is-serving">检索在用</span>}
        {model.configured && <span className="model-tag">配置的模型</span>}
        {!model.hasFingerprint && !retired && <span className="model-tag is-unknown" title="登记时还没有记录编码器指纹">编码器未记录</span>}
        {model.weights && model.weights !== 'ok' && <span className="model-tag is-warn" title="切换、回滚和重启后的配对都要从镜像加载它的权重">{WEIGHTS_TEXT[model.weights]}</span>}
      </div>
      <small>向量库 <code>{model.collection}</code> · {model.dim} 维 · 登记于 {formatLibraryTime(model.createdAt)}{model.activatedAt && ` · 最近切换于 ${formatLibraryTime(model.activatedAt)}`}</small>
      <small className="model-row-weights">权重 <code>{model.weightsUri || '—'}</code>{model.artifact && <> · hash <code title={model.artifact}>{shortArtifact(model.artifact)}</code></>}</small>
    </div>
    <div className="model-row-coverage">
      {retired ? <small>向量库已删除</small> : <Coverage model={model} images={overview.images} />}
      {lostPoints(model) && <small className="is-warn">向量库里实际只有 {model.points} 个向量，有 {model.embedded - (model.points ?? 0)} 张的丢了</small>}
      {!retired && model.behind > 0 && <small>{model.behind} 张的过滤信息落后，{model.status === 'current' ? '后台对账会补推' : '生成向量时一起补推'}</small>}
    </div>
    <div className="model-row-actions">
      {actions.build && <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={onBuild}><Play size={14} />{actions.build}</button>}
      {actions.stop && <button type="button" className="mg-btn mg-secondary" disabled={busy || overview.build?.state === 'stopping'} onClick={onStop}><Square size={14} />停止</button>}
      {actions.activate && <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={onActivate}><ArrowRightLeft size={14} />{actions.activate}</button>}
      {actions.retire && <button type="button" className="mg-btn model-danger-quiet" disabled={busy} onClick={onRetire}><Trash2 size={14} />下线</button>}
    </div>
  </article>
}

/**
 * 模型版本: changing the embedding model without mixing two models' vectors. Each version has its
 * own collection; the configured encoder (EMBEDDING_CONFIG) is registered as a new version, its
 * vectors are built in the background while search stays on the current one, then search switches
 * to it, and the previous version stays for a roll back until it is retired.
 */
export function ModelVersionsPanel({ apiBase }: { apiBase: string }) {
  const versions = useModelVersions(apiBase, (build, overview) => {
    const model = modelById(overview, build.modelId)
    const name = model?.label ?? `版本 ${build.modelId}`
    if (build.state === 'failed') notify.error(`${name} 的生成中止：${build.error ?? '原因未知'}`, { title: '生成向量中止' })
    else if (build.state === 'stopped') notify.info(`已停止为 ${name} 生成向量，本次写入 ${build.embedded} 张。`)
    else if (build.failed > 0) notify.warn(`本次写入 ${build.embedded} 张，${build.failed} 张没生成（见进度里的列表）。`, { title: `${name} 生成完成` })
    else notify.success(`已为 ${name} 生成 ${build.embedded} 张图片的向量。`)
  })
  const { data, loading, error } = versions
  const [pending, setPending] = useState<Pending>(null)
  const [working, setWorking] = useState(false)

  async function act(action: () => Promise<void>, title: string) {
    setWorking(true)
    try {
      await action()
    } catch (cause) {
      notify.error(refusalText(cause), { title })
    } finally {
      setWorking(false)
      void versions.refresh()
    }
  }

  async function register(name: string, version: string) {
    try {
      const model = await registerEmbeddingModel({ name, version }, { baseUrl: apiBase })
      setPending(null)
      notify.success(`已登记 ${model.label}，向量库 ${model.collection} 已建好。下一步：生成向量。`)
    } catch (cause) {
      notify.error(refusalText(cause), { title: '没有登记' })
    } finally {
      void versions.refresh()
    }
  }

  async function activate(model: EmbeddingModel, allowMissing: boolean) {
    try {
      const result = await activateEmbeddingModel(model.id, { allowMissing }, { baseUrl: apiBase })
      setPending(null)
      const previous = data ? modelById(data, result.previousId) : null
      const caught = result.caughtUp > 0 ? `切换时补上了 ${result.caughtUp} 张。` : ''
      const kept = previous ? `${previous.label} 保留，可随时切回。` : ''
      if (!result.switched) notify.info(`检索已经在用 ${result.model.label}。`)
      else if (result.remaining > 0) notify.warn(`检索和入库已改用 ${result.model.label}。还有 ${result.remaining} 张没有向量，补齐前检索不到；后台会逐步补上，也可以「补齐缺少的向量」。${kept}`, { title: '已切换' })
      else notify.success(`检索和入库已改用 ${result.model.label}。${caught}${kept}`)
    } catch (cause) {
      notify.error(refusalText(cause), { title: '没有切换' })
    } finally {
      void versions.refresh()
    }
  }

  async function retire(model: EmbeddingModel) {
    try {
      const result = await retireEmbeddingModel(model.id, { baseUrl: apiBase })
      setPending(null)
      notify.success(`${result.model.label} 已下线，删除了 ${result.ledgerRows} 条写入记录${result.dropped ? '和它的向量库' : ''}。`)
    } catch (cause) {
      notify.error(refusalText(cause), { title: '没有下线' })
    } finally {
      void versions.refresh()
    }
  }

  const build = (model: EmbeddingModel) => act(async () => {
    await startModelBuild(model.id, { baseUrl: apiBase })
    notify.info(`开始为 ${model.label} 生成向量，进度见上方。`)
  }, '没有开始生成')
  const stop = (modelId: number) => act(async () => { await stopModelBuild(modelId, { baseUrl: apiBase }) }, '没有停止')

  if (!data) {
    return <section className="model-versions" aria-label="模型版本" aria-busy={loading}>
      {loading && <p className="model-versions-status" role="status"><LoaderCircle size={15} className="spin" aria-hidden />正在读取模型版本…</p>}
      {error && <div role="alert" className="mg-form-error">
        <p>读取模型版本失败。{refusalText(error)}</p>
        <button type="button" className="mg-btn mg-secondary" disabled={loading} onClick={() => void versions.refresh()}>重试读取</button>
      </div>}
    </section>
  }

  const guide = guideFor(data)
  const action = guide.action
  const serving = modelById(data, data.retrieval.modelId)
  const configuredModel = modelById(data, data.configured.modelId)
  const buildModel = data.build ? modelById(data, data.build.modelId) : null
  const live = data.models.filter(model => model.status !== 'retired')
  const retired = data.models.filter(model => model.status === 'retired')
  const busy = working || loading
  const GuideIcon = guide.tone === 'ok' ? CircleCheck : guide.tone === 'error' ? CircleAlert : guide.tone === 'warn' ? TriangleAlert : Info

  return <section className="model-versions" aria-label="模型版本" aria-busy={loading || working}>
    <div className="mg-metrics">
      <Metric
        label="检索在用"
        value={serving ? serving.label : '不可用'}
        detail={serving ? `向量库 ${serving.collection}${data.retrieval.encoder === 'own' ? '（用它自己的编码器）' : ''}` : (data.retrieval.code ?? '检索服务未就绪')}
        icon={ScanSearch}
      />
      <Metric
        label="配置的模型"
        value={!data.configured.loaded ? '没有加载' : configuredModel ? configuredModel.label : '未登记的新模型'}
        detail={`${data.configured.modelPath ?? 'EMBEDDING_CONFIG.model_path'}${data.configured.artifact ? ` · hash ${shortArtifact(data.configured.artifact)}` : ''}`}
        icon={Cpu}
      />
      <Metric label="图库图片" value={String(data.images)} detail="每个版本都要为每张图片各存一份向量" icon={Images} />
    </div>

    <div className={`model-guide is-${guide.tone}`} role={guide.tone === 'error' ? 'alert' : 'status'}>
      <GuideIcon size={18} aria-hidden />
      <div>
        <strong>{guide.title}</strong>
        <p>{guide.text}</p>
        <Steps step={guide.step} />
      </div>
      {action?.kind === 'register' && <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={() => setPending({ kind: 'register' })}><PackagePlus size={15} />登记为新版本</button>}
      {action?.kind === 'build' && <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={() => void build(action.model)}><Play size={15} />{action.model.status === 'current' ? '补齐缺少的向量' : '生成向量'}</button>}
      {action?.kind === 'activate' && <button type="button" className="mg-btn mg-primary" disabled={busy} onClick={() => setPending({ kind: 'activate', model: action.model })}><ArrowRightLeft size={15} />{action.model.status === 'current' ? '重新启用检索' : `切换到 ${action.model.label}`}</button>}
    </div>

    {data.build && <BuildProgress build={data.build} model={buildModel} images={data.images} stopping={working} onStop={() => void stop(data.build!.modelId)} />}

    <div className="mg-page-toolbar">
      <button type="button" className="mg-btn mg-secondary" disabled={busy} onClick={() => void versions.refresh()}><RefreshCw size={15} />刷新</button>
      {error && <span className="mg-form-error" role="alert">刷新失败，显示上次读取的结果。{refusalText(error)}</span>}
    </div>

    <section className="mg-panel" aria-label="全部模型版本">
      {live.length > 0 ? <div role="list">{live.map(model => <ModelRow
        key={model.id}
        model={model}
        overview={data}
        busy={busy}
        onBuild={() => void build(model)}
        onStop={() => void stop(model.id)}
        onActivate={() => setPending({ kind: 'activate', model })}
        onRetire={() => setPending({ kind: 'retire', model })}
      />)}</div> : <EmptyState icon={Cpu} title="还没有模型版本" description="数据库里没有登记任何模型版本；检索需要一个当前版本才能启动。" />}
      {retired.length > 0 && <details className="model-retired">
        <summary>已下线的版本（{retired.length}）</summary>
        <div role="list">{retired.map(model => <ModelRow key={model.id} model={model} overview={data} busy={busy}
          onBuild={() => undefined} onStop={() => undefined} onActivate={() => undefined} onRetire={() => undefined} />)}</div>
      </details>}
    </section>

    <details className="model-help">
      <summary>换 embedding 模型时会发生什么</summary>
      <ul>
        <li>每个模型版本有自己的向量库。两个模型的向量不能放在一起比，所以检索只用当前版本的编码器查当前版本的向量库。</li>
        <li>改了 EMBEDDING_CONFIG 之后，「图搜图」的全网结果过滤马上用新模型；自建库检索仍用当前版本，直到你在这里切换。</li>
        <li>生成向量在服务端后台进行，用存储里的原图，标注不变；开始时先对照向量库里实际的点核对写入记录，丢了的重新生成；期间新入库、修改和删除的图片也会照顾到，可以随时停止、继续。</li>
        <li>切换时先暂停新的入库请求（排队等待），等进行中的写完，把生成之后新入库的图片（最多 {data.switchCatchUpMax} 张）补上，再切换，所以不会有图片落下。</li>
        <li>切换后原来的版本保留为「可切换」，向量不删，随时切回；切回前它缺的图片可以先「生成向量」（从镜像里加载它自己的权重）。确定不用了再「下线」，才会删除它的向量库。</li>
        <li>不挂载目录时，新旧两个版本的权重都要在镜像里：每个版本一个 <code>config/model/embedding/embeddingcls-&lt;hash&gt;/</code> 目录（<code>python scripts/package_embedding_model.py</code> 生成），旧版本下线后，下次构建镜像才能去掉它的目录。版本行上的「镜像里没有它的权重」提示，就是这个目录不在当前镜像里。</li>
        <li>是不是同一个模型，看预处理、权重文件的 hash 和固定测试图的输出三样都一致；服务重启时会核对：配置的模型和当前版本对不上，就用当前版本记录的权重启动检索；权重也找不到时检索暂停（不会用错模型），在这里切换到新版本即可恢复。</li>
        <li>后台对账每轮会对照向量库抽查一段写入记录，并为当前版本补上没有向量的图片（例如其他进程在切换前写进旧库的），每轮数量见 <code>RECONCILE_CONFIG</code>。</li>
      </ul>
    </details>

    {pending?.kind === 'register' && <RegisterModelDialog modelPath={data.configured.modelPath} dim={data.configured.dim} onClose={() => setPending(null)} onConfirm={register} />}
    {pending?.kind === 'activate' && <ActivateModelDialog model={pending.model} serving={serving} images={data.images} catchUpMax={data.switchCatchUpMax} onClose={() => setPending(null)} onConfirm={allowMissing => activate(pending.model, allowMissing)} />}
    {pending?.kind === 'retire' && <RetireModelDialog model={pending.model} onClose={() => setPending(null)} onConfirm={() => retire(pending.model)} />}
  </section>
}
