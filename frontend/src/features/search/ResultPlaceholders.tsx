import { ChefHat, ImageIcon } from 'lucide-react'

/** Where a result's image would be when none of its sources could be loaded. */
export function ImageUnavailable({ className = '' }: { className?: string }) {
  return <div className={`image-unavailable ${className}`.trim()}><ImageIcon size={32} strokeWidth={1.3} /><span>图片暂不可用</span></div>
}

/** Where an image would be, for 菜谱识别's card: it is dish names, not an image. */
export function RecipeMark({ className = '' }: { className?: string }) {
  return <div className={`recipe-mark ${className}`.trim()}><ChefHat size={32} strokeWidth={1.3} /><span>菜谱识别 · 没有图片</span></div>
}
