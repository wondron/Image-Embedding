import { expect, it } from 'vitest'
import { readJsonFile } from './saveFile'

it('reads JSON saved with a Windows BOM and reports empty, oversized, and malformed files', async () => {
  await expect(readJsonFile(new File(['\ufeff{"categories":[]}'], 'tree.json'))).resolves.toEqual({ categories: [] })
  await expect(readJsonFile(new File([], 'empty.json'))).rejects.toThrow('空文件')
  await expect(readJsonFile(new File(['xxxx'], 'big.json'), 3)).rejects.toThrow('太大')
  await expect(readJsonFile(new File(['{bad'], 'bad.json'))).rejects.toThrow('不是有效的 JSON')
})
