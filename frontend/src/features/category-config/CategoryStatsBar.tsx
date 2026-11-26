import { FEW_IMAGES, type CategoryFocus, type CategoryStats } from './stats'

const count = (value: number) => value.toLocaleString('zh-CN')

/** One count; `id` names it for tests (data-stat). */
function Count({ id, label, value, unit, title }: { id: string; label: string; value: number; unit: string; title?: string }) {
  return <span className="ct-stat" data-stat={id} title={title}>{label} <b>{count(value)}</b> {unit}</span>
}

/** A count that narrows the table to the categories it counts; pressed while it does. */
function CountFilter({ id, label, value, unit, hint, pressed, onToggle }: {
  id: string
  label: string
  value: number
  unit: string
  /** What pressing it shows. */
  hint: string
  pressed: boolean
  onToggle: () => void
}) {
  return <button
    type="button"
    className="ct-stat ct-stat-filter"
    data-stat={id}
    aria-pressed={pressed}
    disabled={!pressed && value === 0}
    title={pressed ? '再点一次取消这个筛选' : hint}
    onClick={onToggle}
  >{label} <b>{count(value)}</b> {unit}</button>
}

/**
 * The footer's counts for what the table is narrowed to (the scope, or the whole tree): how the
 * tree is built on the first line, where its images are labelled on the second. The counts that
 * point at work to do (disabled categories, leaves with no or few images, images labelled above
 * the leaves) are buttons: each narrows the table to those categories.
 */
export function CategoryStatsBar({ stats, scopeName, matched, focus, onFocus, inactiveOnly, onInactiveOnly }: {
  stats: CategoryStats
  /** The domain or group the table is narrowed to; null: the whole tree. */
  scopeName: string | null
  /** Categories the search, status filter or focus matches; null when none is set. */
  matched: number | null
  focus: CategoryFocus | null
  onFocus: (focus: CategoryFocus | null) => void
  /** The status filter shows disabled categories only. */
  inactiveOnly: boolean
  onInactiveOnly: (on: boolean) => void
}) {
  const toggle = (next: CategoryFocus) => onFocus(focus === next ? null : next)
  return <div className="ct-stats" role="group" aria-label="类别统计">
    <div className="ct-stats-row">
      {scopeName && <span className="ct-stats-scope" title={`统计的是「${scopeName}」和它下面的类别`}>{scopeName}</span>}
      {matched !== null && <span className="ct-stat is-matched" data-stat="matched">匹配 <b>{count(matched)}</b> 个</span>}
      <Count id="total" label="类别" value={stats.total} unit="个" />
      {stats.domains > 0 && <Count id="domains" label="类别域" value={stats.domains} unit="个" />}
      <Count id="groups" label="中间类别" value={stats.groups} unit="个" title="类别域以下、还有下级类别的类别" />
      <Count id="leaves" label="叶子类别" value={stats.leaves} unit="个" title="类别域以下、没有下级类别的类别" />
      <CountFilter
        id="inactive"
        label="停用"
        value={stats.inactive}
        unit="个"
        hint="只看停用的类别"
        pressed={inactiveOnly}
        onToggle={() => onInactiveOnly(!inactiveOnly)}
      />
      <Count id="levels" label="最深" value={stats.levels} unit="层" />
    </div>
    <div className="ct-stats-row">
      <Count id="images" label="已标注图片" value={stats.images} unit="张" />
      <Count id="leaf-images" label="标在叶子类别上" value={stats.leafImages} unit="张" />
      <CountFilter
        id="coarse-images"
        label="标在非叶子类别上"
        value={stats.coarseImages}
        unit={`张（${count(stats.coarseCategories)} 个类别）`}
        hint="只看直接标了图片的类别域和中间类别：这些图片没有细分到叶子类别"
        pressed={focus === 'coarse_images'}
        onToggle={() => toggle('coarse_images')}
      />
      <CountFilter
        id="empty-leaves"
        label="无图的叶子类别"
        value={stats.emptyLeaves}
        unit="个"
        hint="只看还没有图片的叶子类别"
        pressed={focus === 'empty_leaves'}
        onToggle={() => toggle('empty_leaves')}
      />
      <CountFilter
        id="few-image-leaves"
        label={`1–${FEW_IMAGES - 1} 张图的叶子类别`}
        value={stats.fewImageLeaves}
        unit="个"
        hint={`只看图片不到 ${FEW_IMAGES} 张的叶子类别`}
        pressed={focus === 'few_image_leaves'}
        onToggle={() => toggle('few_image_leaves')}
      />
    </div>
  </div>
}
