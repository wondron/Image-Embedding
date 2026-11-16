import type { KeyboardEvent } from 'react'
import { Filter, Flame, Lock, Pencil, Plus, ScanSearch, Tag, Trash2 } from 'lucide-react'
import type { Scene, SceneLabel, SceneRule } from '../../entities/scene'
import { EmptyState } from '../../shared/ui'
import { countText, meterWidth, ruleLabel, sceneBrief, sceneCoverage } from './summary'

/** What a scene is made of, one tab each: which images it holds, which labels its results show. */
export type SceneTab = 'rules' | 'labels'
export const SCENE_TABS: readonly SceneTab[] = ['rules', 'labels']

function createdText(value: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return null
  return `${date.toLocaleString('zh-CN', { timeZone: 'America/Denver', hour12: false })} MST`
}

/**
 * One scene: who it is, what it does in a search (told in a sentence from its configuration,
 * with the part of the library it holds), then its rules and labels to edit.
 */
export function SceneDetail({
  scene, isDefault, protectedScene, libraryTotal, pendingSync, canWrite, tab, onTab,
  onEdit, onToggleActive, onDelete, onNewRule, onEditRule, onDeleteRule, onNewLabel, onEditLabel, onToggleLabel, onDeleteLabel,
}: {
  scene: Scene
  isDefault: boolean
  /** The default scene: it cannot be disabled, deleted or stop holding every image. */
  protectedScene: boolean
  libraryTotal: number | null
  /** A configuration change waits for the full sync: the member count is the one before it. */
  pendingSync: boolean
  canWrite: boolean
  tab: SceneTab
  onTab: (tab: SceneTab) => void
  onEdit: () => void
  onToggleActive: () => void
  onDelete: () => void
  onNewRule: () => void
  onEditRule: (rule: SceneRule) => void
  onDeleteRule: (rule: SceneRule) => void
  onNewLabel: () => void
  onEditLabel: (label: SceneLabel) => void
  onToggleLabel: (label: SceneLabel) => void
  onDeleteLabel: (label: SceneLabel) => void
}) {
  const coverage = sceneCoverage(scene, libraryTotal)
  const created = createdText(scene.createdAt)
  const protection = protectedScene ? 'scene-protection-note' : undefined
  const tabs: { key: SceneTab; label: string; count: number }[] = [
    { key: 'rules', label: '收录规则', count: scene.rules.length },
    { key: 'labels', label: '场景标签', count: scene.labels.length },
  ]

  function moveTab(event: KeyboardEvent<HTMLDivElement>) {
    const index = SCENE_TABS.indexOf(tab)
    const next = event.key === 'ArrowRight' ? (index + 1) % SCENE_TABS.length
      : event.key === 'ArrowLeft' ? (index + SCENE_TABS.length - 1) % SCENE_TABS.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? SCENE_TABS.length - 1 : null
    if (next === null) return
    event.preventDefault()
    onTab(SCENE_TABS[next])
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')[next]?.focus()
  }

  return <>
    <header className="scene-hero">
      <div className="scene-hero-main">
        <div className="scene-hero-title">
          <h2 className="scene-hero-name">{scene.name}</h2>
          <code className="scene-code" title="场景编码：搜图接口用它指定场景">{scene.code}</code>
        </div>
        <div className="scene-hero-tags">
          <span className={`scene-tag ${scene.isActive ? 'is-on' : 'is-off'}`}>{scene.isActive ? '启用中' : '已停用'}</span>
          {isDefault && <span className="scene-tag is-default">默认场景</span>}
          {scene.includeAll && <span className="scene-tag">全包含</span>}
          {scene.outputsHeating && <span className="scene-tag is-heat"><Flame size={12} aria-hidden />输出加热信息</span>}
        </div>
      </div>
      <div className="scene-hero-actions">
        <button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={onEdit}><Pencil size={15} />编辑</button>
        <button type="button" className="mg-btn mg-secondary" disabled={!canWrite || protectedScene} aria-describedby={protection} onClick={onToggleActive}>{scene.isActive ? '停用' : '启用'}</button>
        <button type="button" className="mg-btn scene-danger-quiet" disabled={!canWrite || protectedScene} aria-describedby={protection} onClick={onDelete}><Trash2 size={15} />删除</button>
      </div>
      {scene.description && <p className="scene-hero-description">{scene.description}</p>}
      {protectedScene && <p id="scene-protection-note" className="scene-hero-note"><Lock size={13} aria-hidden />没有指定场景的搜图请求都用默认场景，所以它不能停用、删除，也不能取消全包含；名称、说明和加热信息可以改。</p>}
    </header>

    <section className={`scene-brief${scene.isActive ? '' : ' is-off'}`} aria-label="这个场景的作用">
      <ScanSearch size={18} aria-hidden />
      <div className="scene-brief-body">
        <p>{sceneBrief(scene).join('')}</p>
        {scene.isActive && <div className="scene-coverage">
          {coverage.percent !== null && <span className="scene-meter is-wide" aria-hidden="true"><span style={{ width: `${meterWidth(coverage)}%` }} /></span>}
          <span className="scene-coverage-figure">
            {scene.includeAll
              ? <><strong>{countText(coverage.members)}</strong> 张，图库的全部图片</>
              : coverage.total !== null && coverage.percent !== null
                ? <><strong>{countText(coverage.members)}</strong> / {countText(coverage.total)} 张，占图库 {coverage.percentText}</>
                : <><strong>{countText(coverage.members)}</strong> 张</>}
          </span>
          {!scene.includeAll && <span className="scene-coverage-note">张数按上次全量同步统计{pendingSync ? '；有改动还没同步，同步后更新' : ''}</span>}
        </div>}
      </div>
    </section>

    <div className="mg-tabs scene-tabs" role="tablist" aria-label="场景配置" onKeyDown={moveTab}>
      {tabs.map(item => <button
        type="button"
        key={item.key}
        id={`scene-tab-${item.key}`}
        role="tab"
        tabIndex={tab === item.key ? 0 : -1}
        aria-selected={tab === item.key}
        aria-controls={`scene-panel-${item.key}`}
        className={tab === item.key ? 'active' : ''}
        onClick={() => onTab(item.key)}
      >{item.label}<span className="scene-tab-count">{item.count}</span></button>)}
    </div>

    {tab === 'rules' && <div className="scene-pane" role="tabpanel" id="scene-panel-rules" aria-labelledby="scene-tab-rules">
      <div className="scene-pane-head">
        <p>{scene.includeAll
          ? '这是全包含场景，图库里的全部图片都已收录，规则不影响收录。这里的规则只用来把类别对应到场景标签。'
          : '图片的类别命中下面任意一条规则，就收录进这个场景；按类别的规则连同它的全部下级类别一起算。规则还可以指定结果上显示的标签。增删规则或改规则的类别、菜系后，要全量同步，已入库的图片才会重新归属。'}</p>
        <button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={onNewRule}><Plus size={15} />新建规则</button>
      </div>
      {scene.rules.length === 0
        ? <div className="scene-pane-empty"><EmptyState
          icon={Filter}
          title={scene.includeAll ? '还没有规则' : '还没有收录规则'}
          description={scene.includeAll
            ? '全部图片都已收录。想让搜索结果显示场景标签，就添加规则把类别对应到标签。'
            : '添加规则，按类别或菜系把图片收进这个场景。'}
        /></div>
        : <div className="scene-table-scroll"><table className="scene-table scene-rules-table">
          <thead><tr><th>匹配条件</th><th>场景标签</th><th>操作</th></tr></thead>
          <tbody>{scene.rules.map(rule => {
            const label = ruleLabel(scene, rule)
            return <tr key={rule.id}>
              <td>
                <div className="scene-rule-target">
                  <span className={`scene-rule-kind ${rule.category ? 'is-category' : 'is-cuisine'}`}>{rule.category ? '类别' : '菜系'}</span>
                  {rule.category
                    ? <><strong>{rule.category.name ?? rule.category.code}</strong><code>{rule.category.code}</code><small>及全部下级类别</small></>
                    : <><strong>{rule.cuisine}</strong><small>类别的菜系包含它</small></>}
                </div>
              </td>
              <td>
                {rule.label
                  ? <div className="scene-rule-label">
                    <span className={`scene-label-chip${label && !label.isActive ? ' is-off' : ''}`}>{label?.name ?? rule.label.name ?? rule.label.code}</span>
                    <code>{rule.label.code}</code>
                    {label && !label.isActive && <small>标签已停用，结果不显示</small>}
                  </div>
                  : <span className="scene-muted">{rule.category ? '不指定' : '菜系规则不指定标签'}</span>}
              </td>
              <td><div className="scene-table-actions">
                <button type="button" className="text-button" disabled={!canWrite} onClick={() => onEditRule(rule)}>编辑</button>
                <button type="button" className="text-button is-danger" disabled={!canWrite} onClick={() => onDeleteRule(rule)}>删除</button>
              </div></td>
            </tr>
          })}</tbody>
        </table></div>}
    </div>}

    {tab === 'labels' && <div className="scene-pane" role="tabpanel" id="scene-panel-labels" aria-labelledby="scene-tab-labels">
      <div className="scene-pane-head">
        <p>这个场景自己的一套标签。在「收录规则」里把类别对应到标签后，搜索结果的「场景标签」一栏就显示它；一个类别被多条规则覆盖时，用离它最近的那条规则的标签。改动标签立即生效，不需要同步。</p>
        <button type="button" className="mg-btn mg-primary" disabled={!canWrite} onClick={onNewLabel}><Plus size={15} />新建标签</button>
      </div>
      {scene.labels.length === 0
        ? <div className="scene-pane-empty"><EmptyState icon={Tag} title="还没有场景标签" description="添加标签，再到「收录规则」里把类别对应到它。" /></div>
        : <div className="scene-table-scroll"><table className="scene-table scene-labels-table">
          <thead><tr><th>标签</th><th>排序</th><th>状态</th><th>使用它的规则</th><th>操作</th></tr></thead>
          <tbody>{scene.labels.map(label => <tr key={label.id} className={label.isActive ? '' : 'is-off'}>
            <td><div className="scene-rule-label"><span className={`scene-label-chip${label.isActive ? '' : ' is-off'}`}>{label.name}</span><code>{label.code}</code></div></td>
            <td className="scene-number">{label.sortOrder}</td>
            <td><span className={`scene-state ${label.isActive ? 'is-on' : 'is-off'}`}>{label.isActive ? '启用' : '停用'}</span></td>
            <td>{label.ruleCount > 0 ? `${label.ruleCount} 条` : <span className="scene-muted">还没有规则用它</span>}</td>
            <td><div className="scene-table-actions">
              <button type="button" className="text-button" disabled={!canWrite} onClick={() => onEditLabel(label)}>编辑</button>
              <button type="button" className="text-button" disabled={!canWrite} onClick={() => onToggleLabel(label)}>{label.isActive ? '停用' : '启用'}</button>
              <button type="button" className="text-button is-danger" disabled={!canWrite} onClick={() => onDeleteLabel(label)}>删除</button>
            </div></td>
          </tr>)}</tbody>
        </table></div>}
    </div>}

    {created && <p className="scene-created">创建于 {created}</p>}
  </>
}
