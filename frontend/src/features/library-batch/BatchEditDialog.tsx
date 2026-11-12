import { useId, useState, type ReactNode } from 'react'
import { Check, RotateCcw, Square } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { enumOptions, useEnumCatalog } from '../../entities/enum'
import { AMOUNT_FIELDS, FOOD_PROCESSING_ENUM, FOOD_STATE_ENUM, type LibraryImageItem } from '../../entities/image'
import { notify } from '../../shared/ui'
import { TagInput } from '../annotation-fields'
import { CategoryPicker, type CategoryTreeState } from '../category-picker'
import { editOne } from './batchActions'
import { BatchProgress, SelectionStrip } from './BatchProgress'
import {
  EMPTY_BATCH_EDIT,
  changedFields,
  describeBatchEdit,
  validateBatchEdit,
  type BatchEdit,
  type BatchLabelStatus,
  type FieldMode,
  type TagMode,
} from './batchModel'
import { tallyOutcomes, type BatchOutcome } from './runBatch'
import { useBatchRun } from './useBatchRun'
import './library-batch.css'

interface Choice<T extends string> { value: T; label: string }
const FIELD_MODES: Choice<FieldMode>[] = [{ value: 'keep', label: '不修改' }, { value: 'set', label: '改为' }, { value: 'clear', label: '清空' }]
const CATEGORY_MODES: Choice<FieldMode>[] = [{ value: 'keep', label: '不修改' }, { value: 'set', label: '改为' }, { value: 'clear', label: '清空（变为未标注）' }]
const LABEL_MODES: Choice<BatchLabelStatus>[] = [{ value: 'keep', label: '不修改' }, { value: 'verified', label: '已核验' }, { value: 'auto', label: '自动标注' }]
const TAG_MODES: Choice<TagMode>[] = [
  { value: 'keep', label: '不修改' }, { value: 'add', label: '添加' }, { value: 'remove', label: '移除' },
  { value: 'replace', label: '替换为' }, { value: 'clear', label: '清空' },
]

function ModeSelect<T extends string>({ label, value, choices, onChange, disabled = false }: {
  label: string
  value: T
  choices: Choice<T>[]
  onChange: (value: T) => void
  disabled?: boolean
}) {
  return <select className={`lb-mode${value !== 'keep' ? ' is-set' : ''}`} aria-label={`${label}：怎么改`} value={value} disabled={disabled} onChange={event => onChange(event.target.value as T)}>
    {choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
  </select>
}

/** One field: its name, how it changes, and the new value (shown when there is one to give). */
function Row({ label, mode, children, error, hint }: { label: string; mode: ReactNode; children?: ReactNode; error?: string; hint?: string }) {
  return <div className="lb-row">
    <span className="lb-row-label">{label}</span>
    {mode}
    <div className="lb-row-value">
      {children}
      {error ? <p className="lb-error" role="alert">{error}</p> : hint ? <p className="lb-hint">{hint}</p> : null}
    </div>
  </div>
}

export interface BatchEditDialogProps {
  items: readonly LibraryImageItem[]
  apiBase: string
  categories: CategoryTreeState
  onClose: () => void
  /** After each run: some images changed, what shows them reloads. */
  onFinished: (outcomes: ReadonlyMap<string, BatchOutcome>) => void
}

/**
 * 批量修改标注: every field starts at 「不修改」, only what is set is sent, image by image. While it
 * runs the dialog shows the progress and can be stopped; afterwards failures can be retried.
 */
export function BatchEditDialog({ items, apiBase, categories, onClose, onFinished }: BatchEditDialogProps) {
  const id = useId()
  const { catalog } = useEnumCatalog()
  const [edit, setEdit] = useState<BatchEdit>(EMPTY_BATCH_EDIT)
  const [tried, setTried] = useState(false)
  const { run, start, stop } = useBatchRun()
  const errors = validateBatchEdit(edit, categories.tree)
  const shown = tried ? errors : {}
  const fields = changedFields(edit)
  const working = run.phase === 'running' || run.phase === 'stopping'
  const failed = run.items.filter(item => run.outcomes.get(item.id)?.status === 'failed')
  const notReached = run.items.filter(item => !run.outcomes.has(item.id))

  function change(patch: Partial<BatchEdit>) {
    setEdit(current => ({ ...current, ...patch }))
  }

  async function begin(targets: readonly LibraryImageItem[]) {
    const outcomes = await start(items, targets, (item, signal) => editOne(item, edit, apiBase, signal))
    onFinished(outcomes)
    const tally = tallyOutcomes(outcomes.values())
    const clean = outcomes.size === items.length && tally.done === items.length
      && [...outcomes.values()].every(outcome => outcome.status === 'done' && !outcome.note)
    if (clean) {
      notify.success(`已修改 ${items.length} 张图片的${describeBatchEdit(edit)}。`)
      onClose()
    }
  }

  function submit() {
    setTried(true)
    if (!fields.length || Object.keys(errors).length) return
    void begin(items)
  }

  const footer = <div className="lb-footer">
    {run.phase === 'idle'
      ? <>
        <p className="lb-summary">{fields.length ? <>将修改 <strong>{items.length}</strong> 张图片的：{describeBatchEdit(edit)}</> : '还没有要修改的项目：在下面选「改为」「清空」等'}</p>
        <button type="button" className="mg-btn mg-secondary" onClick={onClose}>取消</button>
        <button type="button" className="mg-btn mg-primary" disabled={!fields.length} onClick={submit}><Check size={16} />开始修改</button>
      </>
      : working
        ? <button type="button" className="mg-btn mg-secondary" disabled={run.phase === 'stopping'} onClick={stop}><Square size={14} />{run.phase === 'stopping' ? '正在停止…' : '停止'}</button>
        : <>
          {failed.length > 0 && <button type="button" className="mg-btn mg-secondary" onClick={() => void begin(failed)}><RotateCcw size={15} />重试失败的（{failed.length}）</button>}
          {notReached.length > 0 && <button type="button" className="mg-btn mg-secondary" onClick={() => void begin(notReached)}>继续未处理的（{notReached.length}）</button>}
          <button type="button" className="mg-btn mg-primary" onClick={onClose}>完成</button>
        </>}
  </div>

  return <Dialog title={`批量修改标注（${items.length} 张）`} size="lg" onClose={onClose} dismissDisabled={working} footer={footer}>
    <div className="lb-dialog">
      <SelectionStrip items={items} />
      {run.phase !== 'idle'
        ? <BatchProgress run={run} verb="修改" />
        : <div className="lb-form">
          <p className="lb-intro">只有选了「改为」「添加」「清空」等的项目会写入，其余保持每张图片原样。保存后自动同步到向量库。</p>

          <h3>类别与标注状态</h3>
          <Row
            label="类别"
            mode={<ModeSelect label="类别" value={edit.category.mode} choices={CATEGORY_MODES} onChange={mode => change({ category: { ...edit.category, mode }, labelStatus: mode === 'clear' ? 'keep' : edit.labelStatus })} />}
            error={shown.category}
            hint={edit.category.mode === 'set' ? '改了类别而下面的标注状态「不修改」时，记为已核验。' : undefined}
          >
            {edit.category.mode === 'set' && <CategoryPicker
              id={`${id}-category`}
              label="新的类别"
              tree={categories.tree}
              loading={categories.loading}
              loadError={categories.error}
              onRetry={categories.refresh}
              value={edit.category.categoryId}
              onChange={categoryId => change({ category: { mode: 'set', categoryId } })}
            />}
          </Row>
          <Row
            label="标注状态"
            mode={<ModeSelect label="标注状态" value={edit.labelStatus} choices={LABEL_MODES} disabled={edit.category.mode === 'clear'} onChange={labelStatus => change({ labelStatus })} />}
            error={shown.labelStatus}
            hint={edit.category.mode === 'clear' ? '清空类别后都是未标注。' : edit.labelStatus !== 'keep' ? '没有类别的图片会跳过这一项。' : undefined}
          />

          <h3>标签与描述</h3>
          <Row
            label="标签"
            mode={<ModeSelect label="标签" value={edit.tags.mode} choices={TAG_MODES} onChange={mode => change({ tags: { ...edit.tags, mode } })} />}
            error={shown.tags}
            hint={edit.tags.mode === 'add' ? '加到每张图片已有的标签后面，已有的不重复。' : edit.tags.mode === 'remove' ? '只去掉这里列出的标签，其余保留。' : undefined}
          >
            {(edit.tags.mode === 'add' || edit.tags.mode === 'remove' || edit.tags.mode === 'replace') && <>
              <label className="sr-only" htmlFor={`${id}-tags`}>{edit.tags.mode === 'add' ? '要添加的标签' : edit.tags.mode === 'remove' ? '要移除的标签' : '新的标签'}</label>
              <TagInput
                id={`${id}-tags`}
                tags={edit.tags.values}
                onChange={values => change({ tags: { ...edit.tags, values } })}
                error={shown.tags}
              />
            </>}
          </Row>
          <Row
            label="描述"
            mode={<ModeSelect label="描述" value={edit.description.mode} choices={FIELD_MODES} onChange={mode => change({ description: { ...edit.description, mode } })} />}
            error={shown.description}
          >
            {edit.description.mode === 'set' && <textarea
              className="lb-input"
              aria-label="新的描述"
              rows={3}
              value={edit.description.text}
              onChange={event => change({ description: { mode: 'set', text: event.target.value } })}
            />}
          </Row>

          <h3>食物标注</h3>
          <Row
            label="食物状态"
            mode={<ModeSelect label="食物状态" value={edit.foodState.mode} choices={FIELD_MODES} onChange={mode => change({ foodState: { ...edit.foodState, mode } })} />}
            error={shown.foodState}
          >
            {edit.foodState.mode === 'set' && <select className="lb-input" aria-label="新的食物状态" value={edit.foodState.value} onChange={event => change({ foodState: { mode: 'set', value: event.target.value } })}>
              <option value="">请选择</option>
              {enumOptions(catalog, FOOD_STATE_ENUM).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>}
          </Row>
          <Row
            label="加工状态"
            mode={<ModeSelect label="加工状态" value={edit.foodProcessing.mode} choices={FIELD_MODES} onChange={mode => change({ foodProcessing: { ...edit.foodProcessing, mode } })} />}
            error={shown.foodProcessing}
          >
            {edit.foodProcessing.mode === 'set' && <select className="lb-input" aria-label="新的加工状态" value={edit.foodProcessing.value} onChange={event => change({ foodProcessing: { mode: 'set', value: event.target.value } })}>
              <option value="">请选择</option>
              {enumOptions(catalog, FOOD_PROCESSING_ENUM).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>}
          </Row>
          {AMOUNT_FIELDS.map(field => {
            const amount = edit.amounts[field.key]
            return <Row
              key={field.key}
              label={field.label}
              mode={<ModeSelect label={field.label} value={amount.mode} choices={FIELD_MODES} onChange={mode => change({ amounts: { ...edit.amounts, [field.key]: { ...amount, mode } } })} />}
              error={shown[field.key]}
            >
              {amount.mode === 'set' && <div className="lb-unit">
                <input
                  className="lb-input"
                  inputMode="numeric"
                  aria-label={`新的${field.label}`}
                  placeholder={field.min === 0 ? '≥ 0' : '≥ 1'}
                  value={amount.text}
                  onChange={event => change({ amounts: { ...edit.amounts, [field.key]: { mode: 'set', text: event.target.value } } })}
                />
                {field.unit && <span aria-hidden="true">{field.unit}</span>}
              </div>}
            </Row>
          })}
        </div>}
    </div>
  </Dialog>
}
