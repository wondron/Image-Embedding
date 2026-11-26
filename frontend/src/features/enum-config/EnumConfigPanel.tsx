import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { AlertTriangle, ListTree, LoaderCircle, Plus, RefreshCw } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import {
  ENUM_DESCRIPTION_MAX_LEN,
  ENUM_MAX_VALUES,
  addEnumValue,
  charLength,
  decidePrePost,
  enumValueProblem,
  getEnum,
  listEnums,
  resolveUncertainProbe,
  toEnumFailure,
  useEnumCatalog,
  validateAppendDraft,
  type AppendDraft,
  type EnumFailureView,
  type EnumFieldName,
  type EnumItem,
  type EnumListResult,
  type PrePostDecision,
  type UncertainProbeDecision,
} from '../../entities/enum'
import { EmptyState, notify } from '../../shared/ui'
import '../../components/ManagementViews.css'
import './enum-config.css'

const REQUEST_TIMEOUT_MS = 20_000
/** How long a value just appended stays highlighted in the table. */
const RECENT_MS = 4_000
/** Wide enough for list and detail side by side (enum-config.css stacks them below this). */
const SIDE_BY_SIDE = '(min-width: 761px)'
const EMPTY_DRAFT: AppendDraft = { value: '', label: '', description: '' }

type BlockReason = 'written' | 'unconfirmed'
/** error: the append failed; status: a caution to read before trying again; success: done. */
type NoticeTone = 'error' | 'status' | 'success'
const NOTICE_TONE = { error: 'error', status: 'warn', success: 'success' } as const
type Phase = 'idle' | 'checking' | 'submitting' | 'verifying'
type FileSource = 'file_error' | 'file_invalid'

interface FileAlert {
  message: string
  traceId: string | null
  source: FileSource
}

interface WriteHold {
  message: string
  traceId: string | null
}

type LoadOutcome =
  | { state: 'applied'; list: EnumListResult }
  | { state: 'stale' }
  | { state: 'aborted' }
  | { state: 'failed'; failure: EnumFailureView }

function readEnumParam(): string | null {
  try {
    const name = new URLSearchParams(window.location.search).get('enum')?.trim()
    return name || null
  } catch {
    return null
  }
}

function requestSignal(parent: AbortSignal): AbortSignal {
  const controller = new AbortController()
  const timer = window.setTimeout(() => {
    controller.abort(new DOMException('请求超时', 'TimeoutError'))
  }, REQUEST_TIMEOUT_MS)
  const abortFromParent = () => controller.abort(parent.reason instanceof Error ? parent.reason : new DOMException('Aborted', 'AbortError'))
  if (parent.aborted) abortFromParent()
  else parent.addEventListener('abort', abortFromParent, { once: true })
  controller.signal.addEventListener('abort', () => {
    window.clearTimeout(timer)
    parent.removeEventListener('abort', abortFromParent)
  })
  return controller.signal
}

function replaceItem(items: EnumItem[], item: EnumItem): EnumItem[] {
  const index = items.findIndex(entry => entry.name === item.name)
  if (index < 0) return [...items, item]
  const next = items.slice()
  next[index] = item
  return next
}

function firstField(errors: Partial<Record<EnumFieldName, string>>): EnumFieldName {
  if (errors.value) return 'value'
  if (errors.label) return 'label'
  return 'description'
}

function prePostMessage(reason: Extract<PrePostDecision, { action: 'stop' }>['reason']): string {
  switch (reason) {
    case 'stale': return '枚举状态已变化，这次没有提交。请查看最新提示后再手动提交。'
    case 'file_error': return '枚举文件异常，禁止修改。这次没有提交。'
    case 'missing': return '列表中已没有该枚举，这次没有提交。'
    case 'not_editable': return '该枚举为只读，不能追加。这次没有提交。'
    case 'full': return '该类取值已达 100 个上限，这次没有提交。'
  }
}

function probeMessage(decision: UncertainProbeDecision): string {
  if (decision.action === 'exists') {
    return '核查时发现该取值已经在当前枚举中。无法确认是不是刚才这次请求写入的。列表已按这次查询刷新，请不要把它当成写入成功。'
  }
  if (decision.action === 'allow_retry') {
    return '核查完成：这次查询没有看到该取值，且枚举文件状态正常。不能确认上次是否写入。如果仍要追加，请手动再提交一次。'
  }
  switch (decision.reason) {
    case 'file_error': return '枚举文件异常，已禁止追加。这次核查不能解除禁写。'
    case 'not_editable': return '该枚举当前不可追加。在确认状态变化之前，不能再次提交该取值。'
    case 'missing': return '列表中没有该枚举。在确认该取值不存在之前，不能再次提交。'
    default: return '无法确认该取值是否已写入，核查请求失败。在新鲜查询确认它不存在之前，不能再次提交。'
  }
}

function failureAlert(failure: EnumFailureView): FileAlert {
  return {
    message: failure.detail || '枚举文件异常，禁止修改。',
    traceId: failure.traceId,
    source: 'file_invalid',
  }
}

/** The table row of one value, for scrolling a value just appended into view. */
function valueRowId(name: string, value: string): string {
  return `enum-value-${name}-${value}`
}

interface EnumGroup {
  key: 'editable' | 'readonly'
  title: string
  items: EnumItem[]
}

/** The list in two groups, the enums that take new values first; file order inside each. */
function groupEnums(items: readonly EnumItem[]): EnumGroup[] {
  const groups: EnumGroup[] = [
    { key: 'editable', title: '可追加', items: items.filter(item => item.editable) },
    { key: 'readonly', title: '只读', items: items.filter(item => !item.editable) },
  ]
  return groups.filter(group => group.items.length > 0)
}

export function EnumConfigPanel({ apiBase = '' }: { apiBase?: string }) {
  // Labels shown on the other pages (EnumCatalogProvider): reloaded once a value is written.
  const { refresh: refreshCatalog } = useEnumCatalog()
  const [items, setItems] = useState<EnumItem[]>([])
  const [loading, setLoading] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [listFailure, setListFailure] = useState<EnumFailureView | null>(null)
  const [fileAlert, setFileAlert] = useState<FileAlert | null>(null)
  const [writeHold, setWriteHold] = useState<WriteHold | null>(null)
  const [selectedName, setSelectedName] = useState<string | null>(readEnumParam)
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>(readEnumParam() ? 'detail' : 'list')
  const [detailFailure, setDetailFailure] = useState<EnumFailureView | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailReload, setDetailReload] = useState(0)
  const [reloadKey, setReloadKey] = useState(0)
  const [appendOpen, setAppendOpen] = useState(false)
  const [appendTarget, setAppendTarget] = useState<string | null>(null)
  const [draft, setDraft] = useState<AppendDraft>(EMPTY_DRAFT)
  /** Values appended with 「追加并继续」 while the dialog stayed open. */
  const [appendedCount, setAppendedCount] = useState(0)
  /** The value appended last: highlighted in the table for a moment. */
  const [recent, setRecent] = useState<{ name: string; value: string } | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<EnumFieldName, string>>>({})
  const [focusField, setFocusField] = useState<EnumFieldName | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [refreshPending, setRefreshPending] = useState(false)
  const [blocks, setBlocks] = useState<Record<string, Record<string, BlockReason>>>({})
  const [copiedTrace, setCopiedTrace] = useState<string | null>(null)

  const itemsRef = useRef(items)
  itemsRef.current = items
  const selectedRef = useRef(selectedName)
  selectedRef.current = selectedName
  const blocksRef = useRef(blocks)
  blocksRef.current = blocks
  const appendTargetRef = useRef<string | null>(null)
  const submitLock = useRef(false)
  const mounted = useRef(true)
  const lifetime = useRef(new AbortController())
  const listGen = useRef(0)
  const detailGen = useRef(0)
  const clock = useRef(0)
  const epochs = useRef(new Map<string, number>())
  const appendButtonRef = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(false)
  const refreshPendingRef = useRef(false)
  refreshPendingRef.current = refreshPending

  const writesBlocked = Boolean(fileAlert || writeHold)
  const writesBlockedRef = useRef(writesBlocked)
  writesBlockedRef.current = writesBlocked

  /** The outcome of an append, as a popup (null: nothing to say; a popup already open stays). */
  const setNotice = useCallback((text: string | null, tone: NoticeTone = 'error') => {
    if (text) notify({ type: NOTICE_TONE[tone], message: text, title: tone === 'error' ? '追加失败' : undefined })
  }, [])

  const blockValue = useCallback((name: string, value: string, reason: BlockReason) => {
    const previous = blocksRef.current[name]?.[value]
    const nextReason: BlockReason = reason === 'written' || previous === 'written' ? 'written' : reason
    const next = { ...blocksRef.current, [name]: { ...blocksRef.current[name], [value]: nextReason } }
    blocksRef.current = next
    setBlocks(next)
  }, [])

  const unblockUnconfirmed = useCallback((name: string, value: string) => {
    if (blocksRef.current[name]?.[value] === 'written') return
    const row = { ...blocksRef.current[name] }
    delete row[value]
    const next = { ...blocksRef.current, [name]: row }
    blocksRef.current = next
    setBlocks(next)
  }, [])

  const markWrite = useCallback((name: string) => {
    epochs.current.set(name, ++clock.current)
  }, [])

  const commitList = useCallback((list: EnumListResult, requestClock: number) => {
    setItems(previous => {
      const previousByName = new Map(previous.map(item => [item.name, item]))
      const seen = new Set(list.enums.map(item => item.name))
      const merged = list.enums.map(item => {
        const epoch = epochs.current.get(item.name) ?? 0
        if (epoch > requestClock && previousByName.has(item.name)) return previousByName.get(item.name)!
        return item
      })
      for (const [name, item] of previousByName) {
        if (!seen.has(name) && (epochs.current.get(name) ?? 0) > requestClock) merged.push(item)
      }
      return merged
    })
    if (list.fileError) {
      setFileAlert({ message: list.fileError, traceId: list.traceId, source: 'file_error' })
    } else {
      setFileAlert(null)
    }
    setWriteHold(null)
    setLoadedOnce(true)
  }, [])

  const loadList = useCallback(async (signal: AbortSignal): Promise<LoadOutcome> => {
    const gen = ++listGen.current
    const requestClock = clock.current
    setLoading(true)
    try {
      const list = await listEnums({ baseUrl: apiBase, signal })
      if (!mounted.current) return { state: 'aborted' }
      if (gen !== listGen.current) return { state: 'stale' }
      commitList(list, requestClock)
      setListFailure(null)
      return { state: 'applied', list }
    } catch (error) {
      if (!mounted.current) return { state: 'aborted' }
      if (error instanceof DOMException && error.name === 'AbortError') return { state: 'aborted' }
      if (gen !== listGen.current) return { state: 'stale' }
      const failure = toEnumFailure(error)
      if (failure.blockWrites) setFileAlert(failureAlert(failure))
      setListFailure(failure)
      setLoadedOnce(true)
      return { state: 'failed', failure }
    } finally {
      if (mounted.current && gen === listGen.current) setLoading(false)
    }
  }, [apiBase, commitList])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    lifetime.current = controller
    return () => {
      mounted.current = false
      controller.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void loadList(requestSignal(controller.signal))
    return () => controller.abort()
  }, [loadList, reloadKey])

  useEffect(() => {
    if (!selectedName) return
    const controller = new AbortController()
    const gen = ++detailGen.current
    const requestClock = clock.current
    const name = selectedName
    setDetailLoading(true)
    setDetailFailure(null)
    getEnum(name, { baseUrl: apiBase, signal: requestSignal(controller.signal) }).then(detail => {
      if (!mounted.current || gen !== detailGen.current || selectedRef.current !== name) return
      if ((epochs.current.get(name) ?? 0) > requestClock) {
        setDetailLoading(false)
        return
      }
      setItems(previous => replaceItem(previous, detail.item))
      setDetailLoading(false)
    }).catch(error => {
      if (!mounted.current || gen !== detailGen.current || selectedRef.current !== name) return
      if (error instanceof DOMException && error.name === 'AbortError') return
      const failure = toEnumFailure(error)
      if (failure.blockWrites) setFileAlert(failureAlert(failure))
      setDetailFailure(failure)
      setDetailLoading(false)
    })
    return () => controller.abort()
  }, [apiBase, selectedName, detailReload])

  useEffect(() => {
    try {
      const url = new URL(window.location.href)
      if (url.searchParams.get('view') !== 'enums') return
      if (selectedName) url.searchParams.set('enum', selectedName)
      else url.searchParams.delete('enum')
      const next = `${url.pathname}${url.search}${url.hash}`
      const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
      if (next !== current) window.history.replaceState(null, '', next)
    } catch {
      /* Deep link updates are optional; selection still works in the page. */
    }
  }, [selectedName])

  useEffect(() => {
    if (!focusField) return
    document.getElementById(`enum-append-${focusField}`)?.focus()
    setFocusField(null)
  }, [focusField])

  useEffect(() => {
    if (wasOpen.current && !appendOpen) appendButtonRef.current?.focus()
    wasOpen.current = appendOpen
  }, [appendOpen])

  const groups = useMemo(() => groupEnums(items), [items])

  // Side by side, an empty detail pane has nothing to say: show the first enum of the list.
  // Stacked (narrow screens), the list stays in front until one is picked.
  useEffect(() => {
    if (selectedName !== null || groups.length === 0) return
    if (typeof window.matchMedia !== 'function' || !window.matchMedia(SIDE_BY_SIDE).matches) return
    setSelectedName(groups[0].items[0].name)
  }, [groups, selectedName])

  // A value just appended: bring its row into view, then let the highlight go.
  useEffect(() => {
    if (!recent) return
    document.getElementById(valueRowId(recent.name, recent.value))?.scrollIntoView({ block: 'nearest' })
    const timer = window.setTimeout(() => setRecent(null), RECENT_MS)
    return () => window.clearTimeout(timer)
  }, [recent])

  function selectName(name: string) {
    setSelectedName(name)
    setMobilePane('detail')
  }

  /** ↑ ↓ Home End move through the list, and the detail follows. */
  function moveInList(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') return
    const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-enum]'))
    const current = options.findIndex(option => option === document.activeElement)
    if (current < 0) return
    event.preventDefault()
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? options.length - 1
        : Math.min(options.length - 1, Math.max(0, current + (event.key === 'ArrowDown' ? 1 : -1)))
    options[next].focus()
    const name = options[next].dataset.enum
    if (name) setSelectedName(name)
  }

  /** Typing in a field clears the error the last submit left on it. */
  function editDraft(field: EnumFieldName, value: string) {
    setDraft(current => ({ ...current, [field]: value }))
    setFieldErrors(current => {
      if (!current[field]) return current
      const next = { ...current }
      delete next[field]
      return next
    })
  }

  function openAppend() {
    const name = selectedRef.current
    const item = itemsRef.current.find(entry => entry.name === name)
    if (!item || item.editable !== true || item.values.length >= ENUM_MAX_VALUES || writesBlockedRef.current) return
    appendTargetRef.current = item.name
    setAppendTarget(item.name)
    setDraft(EMPTY_DRAFT)
    setFieldErrors({})
    setAppendedCount(0)
    setNotice(null)
    setAppendOpen(true)
  }

  function closeAppend() {
    if (submitLock.current || phase !== 'idle') return
    setAppendOpen(false)
  }

  async function verifyUncertain(name: string, value: string) {
    blockValue(name, value, 'unconfirmed')
    const outcome = await loadList(requestSignal(lifetime.current.signal))
    if (!mounted.current) return
    if (outcome.state === 'stale' || outcome.state === 'aborted') {
      setNotice('核查尚未完成。在确认该取值不存在之前，不能再次提交。', 'status')
      return
    }
    if (outcome.state === 'failed') {
      setNotice('无法确认该取值是否已写入，核查请求失败。在新鲜查询确认它不存在之前，不能再次提交。', 'status')
      return
    }
    const item = outcome.list.enums.find(entry => entry.name === name) ?? null
    const decision = resolveUncertainProbe({
      probeOk: true,
      fileError: outcome.list.fileError,
      item,
      value,
    })
    if (decision.action === 'allow_retry') unblockUnconfirmed(name, value)
    if (decision.action === 'exists') refreshCatalog()
    setNotice(probeMessage(decision), 'status')
  }

  function applyPostFailure(target: string, failure: EnumFailureView) {
    if (failure.kind === 'not_editable') {
      markWrite(target)
      setItems(previous => previous.map(item => item.name === target ? { ...item, editable: false } : item))
      setNotice('该枚举不可追加。')
      return
    }
    if (failure.kind === 'value_exists') {
      setFieldErrors({ value: failure.fieldErrors.value ?? '该取值已存在。' })
      setFocusField('value')
      setNotice(null)
      return
    }
    if (failure.kind === 'validation') {
      if (Object.keys(failure.fieldErrors).length > 0) {
        setFieldErrors(failure.fieldErrors)
        setFocusField(firstField(failure.fieldErrors))
        setNotice(null)
      } else {
        setNotice(failure.detail)
      }
      return
    }
    if (failure.kind === 'file_invalid') {
      setFileAlert(failureAlert(failure))
      setNotice('枚举文件异常，禁止修改。这次没有自动重试。')
      return
    }
    if (failure.kind === 'write_failed') {
      setNotice(`写入失败。${failure.detail} 请刷新列表核查后再决定是否手动提交，系统不会自动重试。`)
      return
    }
    const code = failure.code ? `（${failure.code}）` : ''
    const trace = failure.traceId ? ` 追踪号：${failure.traceId}` : ''
    setNotice(`${failure.detail}${code}${trace}`)
  }

  /** keepOpen (「追加并继续」): once appended, stay open with blank fields for the next value. */
  async function submit(keepOpen: boolean) {
    if (submitLock.current) return
    const target = appendTargetRef.current
    if (!target) return
    const validated = validateAppendDraft(draft)
    if (!validated.ok) {
      setFieldErrors(validated.fieldErrors)
      setFocusField(firstField(validated.fieldErrors))
      return
    }
    const blocked = blocksRef.current[target]?.[validated.body.value]
    if (blocked) {
      setFieldErrors({
        value: blocked === 'written'
          ? '该取值已写入，不能再次追加。'
          : '还不能确认该取值是否已经写入，不能再次提交。',
      })
      setFocusField('value')
      return
    }
    submitLock.current = true
    setPhase('checking')
    setNotice(null)
    setFieldErrors({})
    try {
      const outcome = await loadList(requestSignal(lifetime.current.signal))
      if (!mounted.current || outcome.state === 'aborted') return
      if (outcome.state === 'stale') {
        setNotice(prePostMessage('stale'), 'status')
        return
      }
      if (outcome.state === 'failed') {
        if (!outcome.failure.blockWrites) {
          setWriteHold({
            message: '提交前未能确认枚举文件状态，已暂停追加。请重试刷新列表。',
            traceId: outcome.failure.traceId,
          })
        }
        setNotice(`${outcome.failure.detail} 这次没有提交。`)
        return
      }
      const freshItem = outcome.list.enums.find(item => item.name === target) ?? null
      const decision = decidePrePost({ fresh: true, fileError: outcome.list.fileError, item: freshItem })
      if (decision.action === 'stop') {
        if (decision.reason === 'not_editable' && freshItem) {
          markWrite(target)
          setItems(previous => replaceItem(previous, { ...freshItem, editable: false }))
        }
        setNotice(prePostMessage(decision.reason))
        return
      }
      if (freshItem?.values.some(row => row.value === validated.body.value)) {
        setFieldErrors({ value: '该取值已存在。' })
        setFocusField('value')
        return
      }
      setPhase('submitting')
      let created: Awaited<ReturnType<typeof addEnumValue>>
      try {
        created = await addEnumValue(target, validated.body, {
          baseUrl: apiBase,
          signal: requestSignal(lifetime.current.signal),
        })
      } catch (error) {
        if (!mounted.current) return
        const failure = toEnumFailure(error)
        if (failure.kind === 'uncertain') {
          setPhase('verifying')
          await verifyUncertain(target, validated.body.value)
          return
        }
        applyPostFailure(target, failure)
        return
      }
      markWrite(target)
      refreshCatalog()
      if (created.outcome === 'created') setItems(previous => replaceItem(previous, created.item))
      blockValue(target, validated.body.value, 'written')
      setRecent({ name: target, value: validated.body.value })
      setAppendedCount(count => count + 1)
      setNotice(`已追加「${validated.body.label}」（${validated.body.value}）。`, 'success')
      const refreshGen = ++detailGen.current
      const refreshClock = clock.current
      try {
        const detail = await getEnum(target, { baseUrl: apiBase, signal: requestSignal(lifetime.current.signal) })
        if (!mounted.current) return
        if (refreshGen === detailGen.current && (epochs.current.get(target) ?? 0) <= refreshClock) {
          setItems(previous => replaceItem(previous, detail.item))
        }
        setRefreshPending(false)
        if (keepOpen) {
          setDraft(EMPTY_DRAFT)
          setFieldErrors({})
          setFocusField('value')
        } else {
          setAppendOpen(false)
        }
      } catch (error) {
        if (!mounted.current || refreshGen !== detailGen.current) return
        const failure = toEnumFailure(error)
        if (failure.blockWrites) setFileAlert(failureAlert(failure))
        setRefreshPending(true)
        setNotice(null)
      }
    } finally {
      submitLock.current = false
      if (mounted.current) setPhase('idle')
    }
  }

  async function refreshManually() {
    if (submitLock.current) return
    const pending = refreshPendingRef.current
    const outcome = await loadList(requestSignal(lifetime.current.signal))
    if (!mounted.current || !pending) return
    if (outcome.state === 'applied') {
      setRefreshPending(false)
      setNotice('已手动刷新。已写入的取值不能再次追加。', 'success')
      return
    }
    if (outcome.state === 'failed') {
      setNotice('已写入，但手动刷新仍未完成。请勿再次追加同一取值。', 'status')
    }
  }

  async function copyTrace(traceId: string) {
    try {
      await navigator.clipboard.writeText(traceId)
      setCopiedTrace(traceId)
    } catch {
      setCopiedTrace(null)
    }
  }

  const selected = items.find(item => item.name === selectedName) ?? null
  const trimmedDraft = draft.value.trim()
  const draftBlock = appendTarget ? blocks[appendTarget]?.[trimmedDraft] : undefined
  const submitDisabled = phase !== 'idle' || writesBlocked || Boolean(draftBlock)
  const submitLabel = phase === 'checking' ? '正在校验…' : phase === 'submitting' ? '提交中…' : phase === 'verifying' ? '正在核查…' : '追加'
  const alertTrace = fileAlert?.traceId ?? writeHold?.traceId ?? null
  const appendDisabled = !selected || !selected.editable || selected.values.length >= ENUM_MAX_VALUES || writesBlocked
  /** Why 追加值 is off, under the enum's name (null while it is on). */
  const appendReason = !selected ? null
    : writesBlocked ? '枚举文件异常，暂时不能追加。'
      : !selected.editable ? '只读：系统按这些取值运行，不能追加。'
        : selected.values.length >= ENUM_MAX_VALUES ? '已达上限，不能继续追加。'
          : null
  const target = appendTarget ? items.find(item => item.name === appendTarget) ?? null : null
  /** Checked while typing: the format, a value the enum already has, a value that must not be sent again. */
  const liveValueError = !trimmedDraft ? null
    : enumValueProblem(trimmedDraft)
      ?? (target?.values.some(row => row.value === trimmedDraft) ? '该取值已存在。' : null)
      ?? (draftBlock === 'written' ? '该取值已写入，不能再次追加。'
        : draftBlock === 'unconfirmed' ? '还不能确认该取值是否已经写入，不能再次提交。' : null)
  const valueError = fieldErrors.value ?? liveValueError
  const descriptionLength = charLength(draft.description)

  return <div className="enum-page">
    {(fileAlert || writeHold) && <div className="mg-errors enum-alert" role="alert">
      <strong>{fileAlert ? '枚举文件异常，禁止修改' : '已暂停追加'}</strong>
      <p>{fileAlert?.message ?? writeHold?.message}</p>
      {alertTrace && <p className="enum-trace">追踪号 {alertTrace}</p>}
      <div className="enum-alert-actions">
        {alertTrace && <button type="button" className="mg-text-button" onClick={() => void copyTrace(alertTrace)}>{copiedTrace === alertTrace ? '已复制追踪号' : '复制追踪号'}</button>}
        <button type="button" className="mg-btn mg-secondary" onClick={() => setReloadKey(key => key + 1)} disabled={phase !== 'idle'}>重试</button>
      </div>
    </div>}

    {listFailure && <div className="mg-errors enum-alert" role="alert">
      <strong>枚举列表加载失败</strong>
      <p>错误码：{listFailure.code ?? '未知'}</p>
      <p>{listFailure.detail}</p>
      <p className="enum-trace">追踪号：{listFailure.traceId ?? '无'}</p>
      <div className="enum-alert-actions">
        {listFailure.traceId && <button type="button" className="mg-text-button" onClick={() => void copyTrace(listFailure.traceId!)}>{copiedTrace === listFailure.traceId ? '已复制追踪号' : '复制追踪号'}</button>}
        <button type="button" className="mg-btn mg-secondary" onClick={() => setReloadKey(key => key + 1)} disabled={phase !== 'idle'}>重试</button>
      </div>
    </div>}

    <div className={`enum-split ${mobilePane === 'detail' ? 'is-detail' : 'is-list'}`}>
      <section className="mg-panel enum-pane enum-list" aria-label="枚举列表">
        <div className="enum-pane-heading"><h2>枚举列表</h2><button type="button" className="mg-text-button" onClick={() => setReloadKey(key => key + 1)} disabled={phase !== 'idle'}>{loading && items.length > 0 ? '正在刷新…' : '刷新'}</button></div>
        {loading && items.length === 0 && <div className="loading-state" role="status" aria-busy="true">
          <LoaderCircle size={22} className="spin" />
          <h3>正在加载枚举…</h3>
          <p>请求 GET /api/enums</p>
          <div className="skeleton-row" aria-hidden="true"><div className="skeleton-card" /><div className="skeleton-card" /><div className="skeleton-card" /></div>
        </div>}
        {!loading && loadedOnce && items.length === 0 && !listFailure && <EmptyState icon={ListTree} title="暂无枚举" description="枚举列表为空，因此不能追加取值。" />}
        {items.length > 0 && <div className="enum-list-scroll" onKeyDown={moveInList}>
          {groups.map(group => <div key={group.key} className="enum-group">
            <h3 className="enum-group-title" id={`enum-group-${group.key}`}>{group.title}<span>{group.items.length}</span></h3>
            <ul className="enum-list-items" aria-labelledby={`enum-group-${group.key}`}>
              {group.items.map(item => <li key={item.name}><button
                type="button"
                className="enum-option"
                data-enum={item.name}
                aria-current={item.name === selectedName ? 'true' : undefined}
                onClick={() => selectName(item.name)}
              >
                <strong>{item.label || item.name}</strong>
                <code>{item.name}</code>
                <span className="enum-count">{item.values.length} 个取值</span>
              </button></li>)}
            </ul>
          </div>)}
        </div>}
      </section>

      <section className="mg-panel enum-pane enum-detail" aria-label="枚举详情">
        <button type="button" className="mg-btn mg-secondary enum-back" onClick={() => setMobilePane('list')}>返回列表</button>
        {!selectedName && <EmptyState icon={ListTree} title="请选择左侧枚举" description="选中一个枚举，查看它的取值；可追加的枚举还能在这里追加新取值。" />}
        {selectedName && !selected && detailLoading && <div className="loading-state" role="status"><LoaderCircle size={22} className="spin" /><h3>正在读取枚举…</h3></div>}
        {selectedName && !selected && !detailLoading && <div className="enum-detail-body">
          {detailFailure
            ? <div className="mg-errors enum-alert" role="alert"><strong>枚举详情加载失败</strong><p>错误码：{detailFailure.code ?? '未知'}</p><p>{detailFailure.detail}</p><p className="enum-trace">追踪号：{detailFailure.traceId ?? '无'}</p><button type="button" className="mg-btn mg-secondary" onClick={() => setDetailReload(key => key + 1)}>重试</button></div>
            : <p className="enum-note">当前列表没有「{selectedName}」。</p>}
        </div>}
        {selected && <div className="enum-detail-body">
          <div className="enum-detail-head">
            <div className="enum-detail-heading">
              <div className="enum-detail-title">
                <h2>{selected.label || selected.name}</h2>
                <code>{selected.name}</code>
                <span className={`enum-badge${selected.editable ? ' enum-badge-editable' : ''}`}>{selected.editable ? '可追加' : '只读'}</span>
              </div>
              {selected.description && <p className="enum-description">{selected.description}</p>}
              <p className="enum-capacity">
                {selected.values.length} 个取值，最多 {ENUM_MAX_VALUES} 个{appendReason && <span className="enum-capacity-reason">{appendReason}</span>}
              </p>
            </div>
            <button
              ref={appendButtonRef}
              type="button"
              className="mg-btn mg-primary enum-append"
              disabled={appendDisabled}
              onClick={openAppend}
            ><Plus size={16} />追加值</button>
          </div>
          {detailFailure && <div className="mg-errors enum-alert" role="alert">
            <strong>详情刷新失败，仍显示已有内容</strong>
            <p>错误码：{detailFailure.code ?? '未知'}</p>
            <p>{detailFailure.detail}</p>
            <p className="enum-trace">追踪号：{detailFailure.traceId ?? '无'}</p>
            <button type="button" className="mg-btn mg-secondary" onClick={() => setDetailReload(key => key + 1)}>重试</button>
          </div>}
          {detailLoading && <p className="enum-note" role="status">正在核对详情…</p>}
          <div className="enum-table-scroll">
            <table className="enum-table">
              <colgroup><col className="enum-col-value" /><col className="enum-col-label" /><col /></colgroup>
              <thead><tr><th scope="col">取值</th><th scope="col">名称</th><th scope="col">说明</th></tr></thead>
              <tbody>
                {selected.values.length === 0
                  ? <tr><td className="enum-empty-values" colSpan={3}>暂无枚举值</td></tr>
                  : selected.values.map(row => <tr
                    key={row.value}
                    id={valueRowId(selected.name, row.value)}
                    className={recent?.name === selected.name && recent.value === row.value ? 'is-new' : undefined}
                  >
                    <td><code>{row.value}</code></td>
                    <td>{row.label || '—'}</td>
                    <td className={row.description ? undefined : 'enum-none'}>{row.description || '—'}</td>
                  </tr>)}
              </tbody>
            </table>
          </div>
        </div>}
      </section>
    </div>

    <div className="mg-info"><AlertTriangle size={17} /><p>已有取值只能查看，不能修改、删除或调整顺序。追加的新取值会马上出现在上传、图片编辑和批量编辑的选项里。类别、场景和加热组不在本页管理。</p></div>

    {appendOpen && <Dialog title="追加枚举值" size="sm" dismissDisabled={phase !== 'idle'} onClose={closeAppend}>
      <form
        className="mg-editor enum-form"
        onSubmit={event => {
          event.preventDefault()
          void submit(false)
        }}
        aria-busy={phase !== 'idle'}
      >
        <div className="mg-editor-body">
          {/* One span: .mg-editor-note is a flex row and would space out every piece of text */}
          <p className="mg-editor-note"><span>追加到「{target?.label || appendTarget}」（<code>{appendTarget}</code>）。提交前会再检查一次枚举文件。</span></p>
          {refreshPending && <p className="enum-refresh-warning" role="status">已写入，刷新未完成，请手动刷新。请勿再次追加同一取值。</p>}
          {target && target.values.length > 0 && <div className="mg-field enum-existing">
            <span className="mg-field-label" id="enum-append-existing">已有的值</span>
            {/* What is taken, and the naming style to follow; the one typed below is marked when it is taken */}
            <ul className="enum-existing-list" aria-labelledby="enum-append-existing">
              {target.values.map(row => <li key={row.value} className={row.value === trimmedDraft ? 'is-match' : undefined}>
                <code>{row.value}</code>{row.label && <span>{row.label}</span>}
              </li>)}
            </ul>
          </div>}
          <label className="mg-field" htmlFor="enum-append-value"><span className="mg-field-label">取值</span>
            <input
              id="enum-append-value"
              data-dialog-initial-focus
              value={draft.value}
              disabled={phase !== 'idle'}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={Boolean(valueError)}
              aria-describedby={valueError ? 'enum-append-value-hint enum-append-value-error' : 'enum-append-value-hint'}
              onChange={event => editDraft('value', event.target.value)}
            />
            <small id="enum-append-value-hint">1–32 个字符，小写字母开头，只用小写字母、数字和下划线。追加后不能修改。</small>
            {valueError && <p
              className="enum-field-error"
              id="enum-append-value-error"
              role={fieldErrors.value ? 'alert' : undefined}
              aria-live={fieldErrors.value ? undefined : 'polite'}
            >{valueError}</p>}
          </label>
          <label className="mg-field" htmlFor="enum-append-label"><span className="mg-field-label">名称</span>
            <input
              id="enum-append-label"
              value={draft.label}
              disabled={phase !== 'idle'}
              autoComplete="off"
              aria-invalid={Boolean(fieldErrors.label)}
              aria-describedby={fieldErrors.label ? 'enum-append-label-hint enum-append-label-error' : 'enum-append-label-hint'}
              onChange={event => editDraft('label', event.target.value)}
            />
            <small id="enum-append-label-hint">选项和结果里显示的名称，最长 64 个字符。</small>
            {fieldErrors.label && <p className="enum-field-error" id="enum-append-label-error" role="alert">{fieldErrors.label}</p>}
          </label>
          <label className="mg-field" htmlFor="enum-append-description"><span className="mg-field-label">说明</span>
            <textarea
              id="enum-append-description"
              rows={3}
              value={draft.description}
              disabled={phase !== 'idle'}
              aria-invalid={Boolean(fieldErrors.description)}
              aria-describedby={fieldErrors.description ? 'enum-append-description-hint enum-append-description-error' : 'enum-append-description-hint'}
              onChange={event => editDraft('description', event.target.value)}
            />
            <small id="enum-append-description-hint" className="enum-hint-row">
              <span>可留空。</span>
              <span className={descriptionLength > ENUM_DESCRIPTION_MAX_LEN ? 'enum-counter is-over' : 'enum-counter'}>{descriptionLength} / {ENUM_DESCRIPTION_MAX_LEN}</span>
            </small>
            {fieldErrors.description && <p className="enum-field-error" id="enum-append-description-error" role="alert">{fieldErrors.description}</p>}
          </label>
        </div>
        <div className="mg-editor-actions">
          {appendedCount > 0 && <span className="enum-appended" role="status">本次已追加 {appendedCount} 个</span>}
          <button type="button" className="mg-btn mg-secondary" onClick={closeAppend} disabled={phase !== 'idle'}>{appendedCount > 0 ? '完成' : '取消'}</button>
          {refreshPending && <button type="button" className="mg-btn mg-secondary" onClick={() => void refreshManually()} disabled={phase !== 'idle'}><RefreshCw size={14} />手动刷新</button>}
          {/* Several values for one enum: no need to reopen the dialog for each */}
          <button
            type="button"
            className="mg-btn mg-secondary"
            disabled={submitDisabled}
            title="追加后留在这里，接着追加下一个"
            onClick={() => void submit(true)}
          >追加并继续</button>
          <button type="submit" className="mg-btn mg-primary" disabled={submitDisabled}>{phase !== 'idle' && <LoaderCircle size={14} className="spin" />}{submitLabel}</button>
        </div>
      </form>
    </Dialog>}
  </div>
}
