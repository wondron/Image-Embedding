import { useRef, useState, type KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { MAX_TAG_LENGTH, MAX_TAGS, charLength } from '../../entities/image'
import './annotation-fields.css'

const SEPARATORS = /[,，、;；\n]/

/**
 * Tags as chips. Enter or a separator (, ， 、 ;) adds what was typed (pasting a list works too);
 * Backspace in the empty input removes the last tag. Tags are trimmed and kept once, like the
 * server stores them (backend text_fields.normalize_tags).
 */
export function TagInput({ id, tags, onChange, disabled = false, error, describedBy }: {
  id: string
  tags: readonly string[]
  onChange: (tags: string[]) => void
  disabled?: boolean
  error?: string
  describedBy?: string
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')

  function add(raw: string) {
    const parts = raw.split(SEPARATORS).map(part => part.trim()).filter(Boolean)
    if (!parts.length) return
    const next = [...tags]
    const issues: string[] = []
    for (const part of parts) {
      if (next.includes(part)) continue
      if (charLength(part) > MAX_TAG_LENGTH) { issues.push(`「${Array.from(part).slice(0, 12).join('')}…」超过 ${MAX_TAG_LENGTH} 个字符`); continue }
      if (next.length >= MAX_TAGS) { issues.push(`最多 ${MAX_TAGS} 个标签`); break }
      next.push(part)
    }
    if (next.length !== tags.length) onChange(next)
    setNotice(issues.join('；'))
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      add(draft)
      setDraft('')
    } else if (event.key === 'Backspace' && !draft && tags.length) {
      onChange(tags.slice(0, -1))
    }
  }

  return <>
    <div className={`ia-tags${disabled ? ' is-disabled' : ''}${error ? ' is-invalid' : ''}`} onClick={() => inputRef.current?.focus()}>
      {tags.map(tag => <span className="ia-tag" key={tag}>
        {tag}
        <button type="button" disabled={disabled} aria-label={`移除标签 ${tag}`} onClick={event => { event.stopPropagation(); onChange(tags.filter(item => item !== tag)); inputRef.current?.focus() }}><X size={12} /></button>
      </span>)}
      <input
        ref={inputRef}
        id={id}
        disabled={disabled}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        placeholder={tags.length ? '' : '输入后回车，如：带皮、切块'}
        value={draft}
        onChange={event => {
          const value = event.target.value
          if (SEPARATORS.test(value)) {
            // Keep the part after the last separator as the new draft
            const tail = value.split(SEPARATORS).pop() ?? ''
            add(value.slice(0, value.length - tail.length))
            setDraft(tail)
          } else setDraft(value)
        }}
        onKeyDown={onKeyDown}
        onBlur={() => { if (draft.trim()) { add(draft); setDraft('') } }}
      />
    </div>
    {notice && <small className="ia-field-note" role="status">{notice}</small>}
  </>
}
