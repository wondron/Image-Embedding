import { useId, type ReactNode } from 'react'

interface ImageFilePickerProps {
  className: string
  label: string
  children: ReactNode
  multiple?: boolean
  disabled?: boolean
  onFiles: (files: File[]) => void
}

/** Let the user's click reach the native input instead of forwarding it with input.click(). */
export function ImageFilePicker({ className, label, children, multiple, disabled = false, onFiles }: ImageFilePickerProps) {
  const id = useId()
  return <label className={`file-picker ${className} ${disabled ? 'is-disabled' : ''}`} htmlFor={id} aria-disabled={disabled || undefined}>
    {children}
    <input id={id} className="file-picker-input" type="file" accept="image/jpeg,image/png,image/webp,image/bmp"
      aria-label={label} multiple={multiple} disabled={disabled} onChange={event => {
        const files = Array.from(event.currentTarget.files ?? [])
        // Clear only the native value so the same file can be selected again.
        // Cancelling the dialog leaves the current query and results untouched.
        event.currentTarget.value = ''
        if (files.length) onFiles(files)
      }} />
  </label>
}
