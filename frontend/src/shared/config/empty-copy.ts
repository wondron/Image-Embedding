/** Product copy for empty / empty-library states (M6 §7). Single config — not page-local. */

export const EMPTY_COPY = {
  searchNever: {
    title: '等待查询图片',
    description: '上传图片或粘贴图片链接后，点击「开始搜索」。',
  },
  searchNoMatch: {
    title: '暂未找到相关图片',
    description: '自建库暂无匹配。可换一张更清晰的图、降低拒识阈值、切换场景，或先去入库。',
    ctaImport: '去入库',
  },
  searchFilteredEmpty: {
    title: '没有符合筛选条件的结果',
    description: '试试其他关键词或类别。',
  },
  libraryEmpty: {
    title: '暂无已入库图片（空库）',
    description: '库已按 PG + S3 重建；当前还没有图片。先整理本地图片再提交入库。',
    ctaImport: '去导入',
  },
  libraryFilteredEmpty: {
    title: '没有匹配的图片',
    description: '当前筛选条件下没有图片，试试放宽或清空筛选，或切回「通用」场景。',
  },
} as const