import { useMemo, useState } from 'react'
import { Archive, FileImage, FileJson, FileText, LoaderCircle } from 'lucide-react'
import { strToU8, zipSync, type Zippable } from 'fflate'
import { Dialog } from '../../components/Dialog'
import type { CategoryTree, HeatingGroupRef } from '../../entities/category'
import { fileTimestamp, saveBlob, saveText } from '../../shared/lib/saveFile'
import { notify } from '../../shared/ui'
import { buildCategoryTreeExport, downloadCategoryTree } from './export'
import { svgToPng } from './raster'
import { buildCategoryTopologySvg, buildCategoryTopologyText, topologyStats } from './topology'
import '../config-transfer/config-transfer.css'

type Format = 'json' | 'txt' | 'svg' | 'png' | 'zip'

const PREVIEW_LINES = 28

const README = [
  '食物类别导出包',
  '',
  'category-tree.json      类别树数据（含停用类别）。可以直接编辑后，在「食材管理 → 上传类别树」导回；',
  '                        也可以作为备份，导入到另一套库（按编码匹配，不依赖 id）。',
  'category-topology.txt   文字版拓扑图，任何文本编辑器都能打开。',
  'category-topology.svg   图像版拓扑图（矢量），用浏览器打开，可任意放大，可贴进文档。',
  'category-topology.png   图像版拓扑图（位图）；类别特别多时可能没有这个文件，请用 SVG。',
  '',
  '上传时的规则：按编码新建或更新（名称、别名、菜系、加热组、排序、启停）；',
  '不会移动、改编码或删除类别；上传后先看到预览，确认后才写入。',
  '',
].join('\r\n')

export interface CategoryExportDialogProps {
  /** The whole tree, as loaded (filters and collapsed rows do not matter here). */
  tree: CategoryTree
  rootCodes: readonly string[]
  heatingGroups: readonly HeatingGroupRef[]
  onClose: () => void
}

/**
 * 「下载类别树」: the data file (JSON, what 「上传类别树」 takes back), and the tree as a topology
 * diagram for people: text, and a picture (SVG, PNG). 「全部打包」 puts them in one ZIP.
 */
export function CategoryExportDialog({ tree, rootCodes, heatingGroups, onClose }: CategoryExportDialogProps) {
  const [busy, setBusy] = useState<Format | null>(null)
  const stats = useMemo(() => topologyStats(tree), [tree])
  const preview = useMemo(() => {
    const lines = buildCategoryTopologyText(tree).trimEnd().split('\n').slice(5)  // the tree itself, without the five heading lines
    return lines.length > PREVIEW_LINES ? [...lines.slice(0, PREVIEW_LINES), `…（共 ${lines.length} 行，下载后查看全部）`].join('\n') : lines.join('\n')
  }, [tree])

  async function download(format: Format) {
    if (busy) return
    setBusy(format)
    const now = new Date()
    const stamp = fileTimestamp(now)
    try {
      if (format === 'json') {
        downloadCategoryTree(buildCategoryTreeExport(tree, rootCodes, heatingGroups, now))
      } else if (format === 'txt') {
        saveText(buildCategoryTopologyText(tree, now), `category-topology-${stamp}.txt`, 'text/plain;charset=utf-8', { bom: true })
      } else if (format === 'svg') {
        saveText(buildCategoryTopologySvg(tree, now).svg, `category-topology-${stamp}.svg`, 'image/svg+xml;charset=utf-8')
      } else if (format === 'png') {
        const picture = buildCategoryTopologySvg(tree, now)
        const png = await svgToPng(picture.svg, picture.width, picture.height)
        saveBlob(png.blob, `category-topology-${stamp}.png`)
      } else {
        const picture = buildCategoryTopologySvg(tree, now)
        const files: Zippable = {
          'category-tree.json': strToU8(buildCategoryTreeExport(tree, rootCodes, heatingGroups, now).content),
          'category-topology.txt': strToU8(`﻿${buildCategoryTopologyText(tree, now)}`),
          'category-topology.svg': strToU8(picture.svg),
          'README.txt': strToU8(`﻿${README}`),
        }
        let pngMissing: string | null = null
        try {
          const png = await svgToPng(picture.svg, picture.width, picture.height)
          files['category-topology.png'] = new Uint8Array(await png.blob.arrayBuffer())
        } catch (error) {
          pngMissing = error instanceof Error ? error.message : 'PNG 生成失败。'
        }
        saveBlob(new Blob([new Uint8Array(zipSync(files, { level: 6 }))], { type: 'application/zip' }), `category-tree-${stamp}.zip`)
        if (pngMissing) {
          notify.warn(`压缩包已发起下载，但里面没有 PNG：${pngMissing}`, { title: '部分文件没有生成' })
          return
        }
      }
      notify.success(`已发起下载，共 ${stats.total} 个类别。`)
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '文件生成失败，请重试。', { title: '下载失败' })
    } finally {
      setBusy(null)
    }
  }

  const button = (format: Format, label = '下载') => (
    <button type="button" className={`mg-btn ${format === 'zip' ? 'mg-primary' : 'mg-secondary'}`} disabled={busy !== null} onClick={() => void download(format)}>
      {busy === format ? <><LoaderCircle size={15} className="spin" />生成中…</> : label}
    </button>
  )

  return <Dialog title="下载类别树" size="lg" onClose={onClose} dismissDisabled={busy !== null}>
    <div className="cx-dialog">
      <p className="cx-note">
        共 {stats.total} 个类别（叶子类别 {stats.leaves} 个，停用 {stats.inactive} 个），最深 {stats.levels} 层。下载的是完整的类别树，包含停用类别，不受页面上的搜索、筛选和折叠影响。
      </p>
      <ul className="cx-downloads">
        <li className="cx-download is-primary">
          <Archive size={20} aria-hidden />
          <div><b>全部打包（ZIP）</b><small>数据文件 + 文字拓扑图 + 图像拓扑图（SVG、PNG），适合做一次完整备份。</small></div>
          {button('zip', '打包下载')}
        </li>
        <li className="cx-download">
          <FileJson size={20} aria-hidden />
          <div><b>数据文件（JSON）</b><small>编码、名称与别名、菜系、加热组、排序、启停。可以编辑后用「上传类别树」导回，也可以导入另一套库。</small></div>
          {button('json')}
        </li>
        <li className="cx-download">
          <FileText size={20} aria-hidden />
          <div><b>文字拓扑图（TXT）</b><small>用树形线条画出的层级，一行一个类别，任何编辑器都能打开。</small></div>
          {button('txt')}
        </li>
        <li className="cx-download">
          <FileImage size={20} aria-hidden />
          <div><b>图像拓扑图（SVG）</b><small>矢量图：浏览器打开后可任意放大，类别再多也清晰。</small></div>
          {button('svg')}
        </li>
        <li className="cx-download">
          <FileImage size={20} aria-hidden />
          <div><b>图像拓扑图（PNG）</b><small>位图，方便贴到聊天或幻灯片里；类别特别多时请用 SVG。</small></div>
          {button('png')}
        </li>
      </ul>
      {stats.total > 0 && <>
        <p className="cx-note">文字拓扑图预览：</p>
        <pre className="cx-preview" aria-label="文字拓扑图预览">{preview}</pre>
      </>}
    </div>
  </Dialog>
}
