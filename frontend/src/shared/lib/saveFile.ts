/** Files in and out of the browser: hand one over to be saved, read a JSON file that was picked. */

/** Ask the browser to save a blob under a name; where it lands is the browser's business. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  try {
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    // Let the save start before the temporary URL goes
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}

/**
 * Save text as a file. bom: start with a UTF-8 byte order mark, which older Windows editors need
 * to read Chinese text right (for .txt meant to be opened by hand; never for JSON).
 */
export function saveText(text: string, filename: string, type = 'text/plain;charset=utf-8', { bom = false } = {}): void {
  saveBlob(new Blob([bom ? '﻿' : '', text], { type }), filename)
}

/** 20 MB: far above any configuration file, and small enough to parse without freezing the page. */
export const JSON_FILE_MAX_BYTES = 20 * 1024 * 1024

/** The parsed content of a picked JSON file. Throws an Error whose message is ready to show. */
export async function readJsonFile(file: File, maxBytes = JSON_FILE_MAX_BYTES): Promise<unknown> {
  if (!file.size) throw new Error(`「${file.name}」是空文件。`)
  if (file.size > maxBytes) throw new Error(`「${file.name}」太大（超过 ${Math.round(maxBytes / 1024 / 1024)} MB），不像是配置文件。`)
  let text: string
  try {
    text = await file.text()
  } catch {
    throw new Error(`读不出「${file.name}」，请重新选择。`)
  }
  try {
    // A byte order mark in front (a file saved by a Windows editor) is not JSON
    return JSON.parse(text.replace(/^﻿/, ''))
  } catch (error) {
    throw new Error(`「${file.name}」不是有效的 JSON 文件：${error instanceof Error ? error.message : '解析失败'}`)
  }
}

/** A timestamp that is safe in a file name: 2026-11-13T19-20-30-456Z. */
export function fileTimestamp(date = new Date()): string {
  return date.toISOString().replace(/[:.]/g, '-')
}
