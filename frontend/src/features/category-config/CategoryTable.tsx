import type { CSSProperties } from 'react'
import { ChevronDown, ChevronRight, Folder, FolderOpen, Leaf, Pencil, Plus, Trash2 } from 'lucide-react'
import {
  categoryLabel,
  effectiveCuisines,
  effectiveHeatingGroup,
  type CategoryNode,
  type CategoryTree,
  type CategoryTreeNode,
} from '../../entities/category'
import { otherCategoryNames, type CategoryTableRow, type CategoryTableView } from './tableModel'

/** Chips shown in the 其他名称 cell before 「+N」. */
const NAMES_SHOWN = 3

interface RowActions {
  onToggle: (id: number) => void
  onEdit: (node: CategoryNode) => void
  onAddChild: (node: CategoryNode) => void
  onDelete: (entry: CategoryTreeNode) => void
}

interface Context extends RowActions {
  tree: CategoryTree
  /** Images on each node or below it (subtreeImageCounts). */
  counts: ReadonlyMap<number, number>
  rows: ReadonlyMap<number, { row: CategoryTableRow; index: number }>
  last: number
  filtering: boolean
  selectedId: number | null
  busy: boolean
}

export function rowElementId(id: number): string {
  return `category-row-${id}`
}

function NamesCell({ node }: { node: CategoryNode }) {
  const names = otherCategoryNames(node.names)
  if (names.length === 0) return <span className="ct-none">—</span>
  const describe = names.map(name => `${name.lang} ${name.name}（${name.kind === 'primary' ? '主名' : '别名'}）`).join('\n')
  return <span className="ct-chips" title={describe}>
    {names.slice(0, NAMES_SHOWN).map(name => <span className="ct-chip" key={`${name.lang}\n${name.name}`}>
      {name.lang !== 'zh' && <i>{name.lang}</i>}{name.name}
    </span>)}
    {names.length > NAMES_SHOWN && <span className="ct-more">+{names.length - NAMES_SHOWN}</span>}
  </span>
}

/** A value the node inherits: muted, with where it comes from. */
function Inherited({ text, from }: { text: string; from: CategoryNode }) {
  return <span className="ct-inherited" title={`沿用上级「${categoryLabel(from)}」的设置`}>
    <span className="ct-inherited-value">{text}</span><small>沿用</small>
  </span>
}

function CuisinesCell({ tree, node }: { tree: CategoryTree; node: CategoryNode }) {
  const cuisines = effectiveCuisines(tree, node.id)
  if (cuisines.value.length === 0) return <span className="ct-none">—</span>
  if (cuisines.from) return <Inherited text={cuisines.value.join('、')} from={cuisines.from} />
  return <span className="ct-chips">{cuisines.value.map(cuisine => <span className="ct-chip" key={cuisine}>{cuisine}</span>)}</span>
}

function HeatingCell({ tree, node }: { tree: CategoryTree; node: CategoryNode }) {
  const group = effectiveHeatingGroup(tree, node.id)
  if (!group.value) return <span className="ct-none">—</span>
  if (group.from) return <Inherited text={group.value.name} from={group.from} />
  return <span className="ct-heating" title={group.value.code}>{group.value.name}</span>
}

/** What uses the node itself, then the images on the categories below it (a group has few of its own). */
function UsageCell({ node, total }: { node: CategoryNode; total: number }) {
  const below = Math.max(0, total - node.imageCount)
  const uses = [
    node.imageCount > 0 ? `${node.imageCount} 张图片` : null,
    node.sceneRuleCount > 0 ? `${node.sceneRuleCount} 条规则` : null,
    below > 0 ? `下级 ${below} 张` : null,
  ].filter((text): text is string => text !== null)
  if (uses.length === 0) return <span className="ct-none">—</span>
  const text = uses.join(' · ')
  return <span className="ct-usage" title={below > 0 ? `${text}（下级类别上的图片合计，不分场景）` : text}>{text}</span>
}

function StateBadge({ active, inline = false }: { active: boolean; inline?: boolean }) {
  return <span className={`ct-state ${active ? 'is-active' : 'is-inactive'}${inline ? ' ct-state-inline' : ''}`}>{active ? '启用' : '停用'}</span>
}

function Row({ entry, ctx }: { entry: CategoryTreeNode; ctx: Context }) {
  const { node, children } = entry
  const { row, index } = ctx.rows.get(node.id)!
  const label = categoryLabel(node)
  const selected = node.id === ctx.selectedId
  const classes = [
    'ct-row', 'ct-grid',
    index % 2 === 1 ? 'is-odd' : '',
    index === ctx.last ? 'is-last' : '',
    selected ? 'is-selected' : '',
    row.context ? 'is-context' : '',
    node.isActive ? '' : 'is-inactive',
  ].filter(Boolean).join(' ')
  const Icon = children.length === 0 ? Leaf : row.open ? FolderOpen : Folder
  return <li>
    <div className={classes} id={rowElementId(node.id)}>
      <div className="ct-cell ct-col-name" style={{ '--ct-depth': row.depth } as CSSProperties}>
        {children.length > 0 && !ctx.filtering
          ? <button
            type="button"
            className="ct-toggle"
            aria-expanded={row.open}
            aria-label={`${row.open ? '收起' : '展开'}「${label}」`}
            title={`${children.length} 个下级类别`}
            onClick={() => ctx.onToggle(node.id)}
          >{row.open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
          : <span className="ct-toggle ct-toggle-static" aria-hidden="true">{row.open && <ChevronDown size={15} />}</span>}
        <span className="ct-icon" aria-hidden="true"><Icon size={16} strokeWidth={1.7} /></span>
        <button
          type="button"
          className="ct-label"
          aria-current={selected ? 'true' : undefined}
          title={`编辑「${label}」`}
          onClick={() => ctx.onEdit(node)}
        >
          <span className="ct-label-line">
            <strong>{label}</strong>
            {children.length > 0 && <span className="ct-count" aria-hidden="true">{children.length}</span>}
            {!node.isActive && <StateBadge active={false} inline />}
          </span>
          {/* Cut at the front when the cell is narrow: the last segment is the node's own (bdo keeps the code left-to-right) */}
          <code className="ct-code" title={node.code}><bdo dir="ltr">{node.code}</bdo></code>
        </button>
      </div>
      <div className="ct-cell ct-col-names"><NamesCell node={node} /></div>
      <div className="ct-cell ct-col-cuisines"><CuisinesCell tree={ctx.tree} node={node} /></div>
      <div className="ct-cell ct-col-heating"><HeatingCell tree={ctx.tree} node={node} /></div>
      <div className="ct-cell ct-col-usage"><UsageCell node={node} total={ctx.counts.get(node.id) ?? node.imageCount} /></div>
      <div className="ct-cell ct-col-status"><StateBadge active={node.isActive} /></div>
      <div className="ct-cell ct-col-actions mg-row-actions">
        <button type="button" aria-label={`新建「${label}」的下级类别`} title="新建下级类别" disabled={ctx.busy} onClick={() => ctx.onAddChild(node)}><Plus size={16} /></button>
        <button type="button" aria-label={`编辑「${label}」`} title="编辑" disabled={ctx.busy} onClick={() => ctx.onEdit(node)}><Pencil size={15} /></button>
        <button type="button" aria-label={`删除「${label}」`} title="删除" disabled={ctx.busy} onClick={() => ctx.onDelete(entry)}><Trash2 size={15} /></button>
      </div>
    </div>
    {row.open && <RowList entries={children} ctx={ctx} />}
  </li>
}

function RowList({ entries, ctx }: { entries: readonly CategoryTreeNode[]; ctx: Context }) {
  const shown = entries.filter(entry => ctx.rows.has(entry.node.id))
  if (shown.length === 0) return null
  // role="list": list-style: none drops the list role in Safari
  return <ul className="ct-list" role="list">
    {shown.map(entry => <Row key={entry.node.id} entry={entry} ctx={ctx} />)}
  </ul>
}

/**
 * The category tree as a table: one row per listed node, nested lists for the levels, so the
 * columns line up while assistive technology still hears the hierarchy. One line per row: with
 * hundreds of categories the height of a row decides how much of a group fits on the screen.
 */
export function CategoryTable({ tree, view, counts, selectedId, busy, ...actions }: RowActions & {
  tree: CategoryTree
  view: CategoryTableView
  counts: ReadonlyMap<number, number>
  selectedId: number | null
  busy: boolean
}) {
  const ctx: Context = {
    ...actions,
    tree,
    counts,
    rows: new Map(view.rows.map((row, index) => [row.entry.node.id, { row, index }])),
    last: view.rows.length - 1,
    filtering: view.filtering,
    selectedId,
    busy,
  }
  return <div className="ct-table">
    <div className="mg-table-head ct-head ct-grid">
      <span className="ct-col-name">名称 / 编码</span>
      <span className="ct-col-names">其他名称</span>
      <span className="ct-col-cuisines">菜系</span>
      <span className="ct-col-heating">加热组</span>
      <span className="ct-col-usage">使用</span>
      <span className="ct-col-status">状态</span>
      <span className="ct-col-actions">操作</span>
    </div>
    <RowList entries={view.rows.filter(row => row.depth === 0).map(row => row.entry)} ctx={ctx} />
  </div>
}
