import { categoryLabel, flattenCategoryTree, type CategoryTree, type CategoryTreeNode } from '../../entities/category'

/**
 * The category tree as a topology diagram, for people: a text version (box-drawing characters, opens
 * anywhere) and a picture (SVG, left to right: a domain, its groups, their categories). Both show
 * every node, disabled ones included, in the page's order. Pure functions of the tree: the download
 * dialog saves what they return, and turns the SVG into a PNG.
 */

export interface TopologyStats {
  total: number
  inactive: number
  /** Categories without children. */
  leaves: number
  /** Levels of the deepest branch (a tree of only domains has 1). */
  depth: number
  /** Images labelled with any category. */
  images: number
}

export function topologyStats(tree: CategoryTree): TopologyStats {
  const stats: TopologyStats = { total: 0, inactive: 0, leaves: 0, depth: 0, images: 0 }
  for (const { node, depth, children } of flattenCategoryTree(tree)) {
    stats.total += 1
    if (!node.isActive) stats.inactive += 1
    if (children.length === 0) stats.leaves += 1
    stats.depth = Math.max(stats.depth, depth + 1)
    stats.images += node.imageCount
  }
  return stats
}

function statsLine(stats: TopologyStats): string {
  return `类别 ${stats.total} 个（停用 ${stats.inactive} 个） · 末级类别 ${stats.leaves} 个 · 最深 ${stats.depth} 层 · 已标注图片 ${stats.images} 张`
}

// -- text ----------------------------------------------------------------------------------

function describe({ node }: CategoryTreeNode): string {
  const parts = [`${categoryLabel(node)} [${node.code}]`]
  if (!node.isActive) parts.push('「停用」')
  if (node.cuisines.length) parts.push(`菜系: ${node.cuisines.join(', ')}`)
  if (node.heatingGroup) parts.push(`加热组: ${node.heatingGroup.name}`)
  if (node.imageCount > 0) parts.push(`图 ${node.imageCount}`)
  return parts.join('  ')
}

/**
 * The tree drawn with box-drawing characters, one category per line:
 *
 *     食材 [ingredient]
 *     ├── 肉类 [ingredient.meat]  菜系: chinese
 *     │   └── 牛肉 [ingredient.meat.beef]  图 12
 *     └── 蔬菜 [ingredient.vegetable]  「停用」
 */
export function buildCategoryTopologyText(tree: CategoryTree, exportedAt = new Date()): string {
  const lines = [
    '食物类别拓扑图',
    `导出时间：${exportedAt.toISOString()}`,
    statsLine(topologyStats(tree)),
    '图例：名称 [编码]；「停用」为已停用；菜系、加热组是节点自己设置的（没写的沿用上级）；图 N 为直接标在该类别上的图片数',
    '',
  ]
  const visit = (entry: CategoryTreeNode, lead: string, childLead: string): void => {
    lines.push(`${lead}${describe(entry)}`)
    entry.children.forEach((child, index) => {
      const last = index === entry.children.length - 1
      visit(child, `${childLead}${last ? '└── ' : '├── '}`, `${childLead}${last ? '    ' : '│   '}`)
    })
  }
  if (tree.roots.length === 0) lines.push('（还没有类别）')
  for (const root of tree.roots) visit(root, '', '')
  return `${lines.join('\n')}\n`
}

// -- picture -------------------------------------------------------------------------------

export interface TopologySvg {
  svg: string
  /** CSS pixels at scale 1 (the viewBox). */
  width: number
  height: number
}

const FONT = 13
const SMALL_FONT = 11
const ROW = 26
const PILL = 20
const PILL_PAD = 9
const GAP = 46
const PAD = 28
const HEADER = 92
const MIN_WIDTH = 720
const FONT_FAMILY = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Segoe UI',Arial,sans-serif"
/** One hue per domain (root), reused in order when a tree has more roots. */
const DOMAIN_COLORS = ['#2c56a0', '#2f7d4a', '#b7791f', '#a23b3b', '#6b46c1', '#1f7a80'] as const
const INACTIVE = '#9aa3af'

/** Width of a run of text, estimated without a canvas: CJK glyphs are square, Latin ones narrower. */
function textWidth(text: string, size: number): number {
  let units = 0
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0
    if (code > 0x2e7f) units += 1
    else if (char === ' ') units += 0.34
    else if (/[A-Z0-9_]/.test(char)) units += 0.66
    else if (/[a-z]/.test(char)) units += 0.56
    else units += 0.6
  }
  return units * size * 1.04
}

function escapeXml(text: string): string {
  return text
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

const px = (value: number) => String(Math.round(value * 10) / 10)

interface Placed {
  entry: CategoryTreeNode
  depth: number
  /** Vertical centre. */
  y: number
  name: string
  /** The code's last segment (the whole code for a domain), and what else the node says. */
  aside: string
  width: number
  color: string
  children: Placed[]
}

function place(entry: CategoryTreeNode, depth: number, color: string, nextRow: { value: number }, all: Placed[]): Placed {
  const { node } = entry
  const name = categoryLabel(node)
  const segment = node.code.slice(node.code.lastIndexOf('.') + 1)
  const aside = [segment === name ? '' : segment, node.isActive ? '' : '停用', node.imageCount > 0 ? `${node.imageCount} 图` : '']
    .filter(Boolean).join(' · ')
  const placed: Placed = {
    entry, depth, y: 0, name, aside, color,
    width: PILL_PAD * 2 + textWidth(name, FONT) + (aside ? 7 + textWidth(aside, SMALL_FONT) : 0),
    children: [],
  }
  all.push(placed)
  placed.children = entry.children.map(child => place(child, depth + 1, color, nextRow, all))
  placed.y = placed.children.length
    ? (placed.children[0].y + placed.children[placed.children.length - 1].y) / 2
    : HEADER + (nextRow.value++) * ROW + ROW / 2
  return placed
}

/**
 * The tree as a left-to-right diagram: one column per level, each category a pill joined to its
 * parent by an elbow line, a leaf per row. Domains keep one colour through their subtree; disabled
 * categories are grey with a dashed outline. Light background, system fonts, no external resource,
 * so the file opens in a browser, pastes into a document, and can be drawn to a canvas for a PNG.
 */
export function buildCategoryTopologySvg(tree: CategoryTree, exportedAt = new Date()): TopologySvg {
  const all: Placed[] = []
  const nextRow = { value: 0 }
  const roots = tree.roots.map((root, index) => place(root, 0, DOMAIN_COLORS[index % DOMAIN_COLORS.length], nextRow, all))
  const stats = topologyStats(tree)

  const columnWidths: number[] = []
  for (const node of all) columnWidths[node.depth] = Math.max(columnWidths[node.depth] ?? 0, node.width)
  const columnX: number[] = []
  let cursor = PAD
  columnWidths.forEach((width, depth) => {
    columnX[depth] = cursor
    cursor += width + GAP
  })
  const legend = '图例：实线框为启用，灰色虚线框为停用；名称后是编码的最后一段；「N 图」为直接标在该类别上的图片数'
  const subtitle = `${statsLine(stats)} · 导出时间 ${exportedAt.toISOString()}`
  const width = Math.ceil(Math.max(
    MIN_WIDTH,
    cursor - GAP + PAD,
    PAD * 2 + textWidth(subtitle, 12),
    PAD * 2 + textWidth(legend, 12),
  ))
  const height = Math.ceil(HEADER + Math.max(1, nextRow.value) * ROW + PAD)

  const links: string[] = []
  const pills: string[] = []
  const labels: string[] = []
  for (const node of all) {
    const x = columnX[node.depth]
    const active = node.entry.node.isActive
    const stroke = active ? node.color : INACTIVE
    pills.push(
      `<rect x="${px(x)}" y="${px(node.y - PILL / 2)}" width="${px(node.width)}" height="${PILL}" rx="6" `
      + `fill="${active ? node.color : '#f3f4f6'}" fill-opacity="${active ? '0.09' : '1'}" `
      + `stroke="${stroke}" stroke-opacity="${active ? '0.4' : '0.7'}"${active ? '' : ' stroke-dasharray="3 3"'}/>`,
    )
    const weight = node.children.length ? ' font-weight="600"' : ''
    labels.push(
      `<text x="${px(x + PILL_PAD)}" y="${px(node.y + 4.5)}"><tspan${weight} fill="${active ? '#1f2937' : '#6b7280'}">${escapeXml(node.name)}</tspan>`
      + (node.aside ? `<tspan dx="7" font-size="${SMALL_FONT}" fill="${active ? '#6b7280' : '#9aa3af'}">${escapeXml(node.aside)}</tspan>` : '')
      + '</text>',
    )
    if (!node.children.length) continue
    const childX = columnX[node.depth + 1]
    const middle = childX - GAP / 2
    const first = node.children[0].y
    const last = node.children[node.children.length - 1].y
    let path = `M${px(x + node.width)} ${px(node.y)}H${px(middle)}`
    if (last > first) path += `M${px(middle)} ${px(first)}V${px(last)}`
    for (const child of node.children) path += `M${px(middle)} ${px(child.y)}H${px(childX)}`
    links.push(`<path d="${path}" stroke="${node.color}"/>`)
  }

  const domains = roots.map((root, index) => {
    const x = PAD + roots.slice(0, index).reduce((sum, entry) => sum + 26 + textWidth(entry.name, 12), 0)
    return `<circle cx="${px(x + 5)}" cy="72" r="4.5" fill="${root.color}"/><text x="${px(x + 14)}" y="76" font-size="12" fill="#374151">${escapeXml(root.name)}</text>`
  })

  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="${FONT_FAMILY}" font-size="${FONT}">`,
    `<rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `<text x="${PAD}" y="34" font-size="18" font-weight="700" fill="#111827">食物类别拓扑图</text>`,
    `<text x="${PAD}" y="54" font-size="12" fill="#6b7280">${escapeXml(subtitle)}</text>`,
    ...domains,
    all.length
      ? `<text x="${PAD}" y="${height - 9}" font-size="11" fill="#9aa3af">${escapeXml(legend)}</text>`
      : `<text x="${PAD}" y="${HEADER + 18}" font-size="13" fill="#9aa3af">（还没有类别）</text>`,
    `<g fill="none" stroke-width="1.2" stroke-opacity="0.45">${links.join('')}</g>`,
    `<g stroke-width="1">${pills.join('')}</g>`,
    `<g>${labels.join('')}</g>`,
    '</svg>',
  ].join('\n')
  return { svg, width, height }
}
