import os, random, cv2, io
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from typing import Any, Iterator, Mapping, Tuple, Literal
import logging

logger = logging.getLogger('imageutils')


def iter_image_urls(item: Mapping[str, Any]) -> Iterator[str]:
    """按原图、large、medium、small、其他缩略图的顺序返回不重复的地址，不发起请求。"""
    original = item.get('image_url')
    candidates = [original]
    # 上游没有原图时，image_url 也可能是缩略图字典。
    thumbnails = [original] if isinstance(original, Mapping) else []
    thumbnails.append(item.get('thumbnail_url'))
    details = item.get('details')
    images = details.get('images') if isinstance(details, Mapping) else None
    if isinstance(images, (list, tuple)):
        thumbnails.extend(image.get('thumbnail_url') for image in images if isinstance(image, Mapping))

    sizes = ('large', 'medium', 'small')
    for size in sizes:
        for thumbnail in thumbnails:
            if isinstance(thumbnail, Mapping):
                candidates.append(thumbnail.get(size))
            elif size == 'large':
                candidates.append(thumbnail)
    for thumbnail in thumbnails:
        if isinstance(thumbnail, Mapping):
            candidates.extend(url for size, url in thumbnail.items() if size not in sizes)

    seen = set()
    for url in candidates:
        if not isinstance(url, str):
            continue
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            yield url


def download_image_bgr(url: str, timeout: float = 10) -> np.ndarray | None:
    """按需下载并解码为 uint8 HWC BGR；请求或解码失败时返回 None。"""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        with requests.get(url.strip(), timeout=timeout) as response:
            response.raise_for_status()
            data = response.content
        if not data:
            return None
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image if image is not None and image.size else None
    except (requests.RequestException, OSError, ValueError, cv2.error) as exc:
        logger.warning('Failed to download image %s: %s', url, exc)
        return None


def resolve_image_bgr(item: Mapping[str, Any], timeout: float = 10) -> tuple[str | None, np.ndarray | None]:
    """按需尝试原图和缩略图，返回 (可用 URL, BGR)，全部失败返回 (None, None)。

    不修改 item；调用方决定是否将验证后的 URL 写回结果。
    """
    for url in iter_image_urls(item):
        image = download_image_bgr(url, timeout=timeout)
        if image is not None:
            return url, image
    return None, None


def normalize_to_u8(x: np.ndarray) -> np.ndarray:
    """
    归一化到 [0,255] 的 uint8 格式
    """
    x = x.astype(np.float32, copy=False)
    if not np.isfinite(x).all():
        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    vmin = float(x.min())
    vmax = float(x.max())
    if vmax - vmin < 1e-8:
        return np.zeros_like(x, dtype=np.uint8)
    x = (x - vmin) / (vmax - vmin) * 255.0
    return x.astype(np.uint8, copy=False)


def overlay_mask(
    img_bgr: np.ndarray,
    vis: np.ndarray,
    alpha: float = 0.5,
    mode: Literal["overlay", "stroke"] = "overlay",
    color: tuple[int, int, int] = (0, 255, 0),
    stroke_width: int = 2,
) -> np.ndarray:
    assert img_bgr.ndim == 3 and img_bgr.shape[2] == 3, "img_bgr must be HxWx3"
    H, W = img_bgr.shape[:2]

    mask = cv2.resize(vis, (W, H), interpolation=cv2.INTER_NEAREST)

    if mask.dtype != np.uint8 or mask.max() <= 1:
        mask_u8 = normalize_to_u8(mask)
    else:
        mask_u8 = mask.astype(np.uint8, copy=False)

    fg = (mask_u8 > 0).astype(np.uint8)
    out = img_bgr.copy()

    if mode == "overlay":
        # ---- 新增：单色模式 ----
        color_arr = np.zeros_like(img_bgr, dtype=np.float32)
        color_arr[:] = color  # 填充指定颜色

        a = (fg.astype(np.float32)[..., None]) * float(np.clip(alpha, 0.0, 1.0))
        base = img_bgr.astype(np.float32)
        out = (base * (1.0 - a) + color_arr * a).astype(np.uint8)

    elif mode == "stroke":
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        w = max(1, int(stroke_width))
        cv2.drawContours(out, contours, -1, color, thickness=w, lineType=cv2.LINE_AA)

    return out


# 变换成正方形图像
def trans_image(pre_method, resize_val, origin_image):
    ori_image = origin_image
    if ori_image is None:
        raise ValueError(f"无法读取图像数据！")
    
    output_size = resize_val
    image = ori_image.copy()
    # 直接变换
    if pre_method == 0 :
        image = cv2.resize(image, (output_size, output_size))
    # 等比例图像变换
    elif pre_method == 1 :
        original_height, original_width = image.shape[:2]
        aspect_ratio = original_width / original_height
        if aspect_ratio > 1:
            # 宽图像，调整宽度，保持原始宽高比
            new_width = output_size
            new_height = int(output_size / aspect_ratio)
        else:
            # 高图像，调整高度，保持原始宽高比
            new_height = output_size
            new_width = int(output_size * aspect_ratio)
        new_size = (new_width, new_height)
            
        resized_img = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
        # 创建目标大小的图像，并填充为黑色（0值）
        padded_image = np.zeros((output_size, output_size, 3), dtype=np.uint8)
         # 计算边缘填充的起始位置
        x_offset = (output_size - new_size[0]) // 2
        y_offset = (output_size - new_size[1]) // 2

        padded_image[y_offset:y_offset + new_size[1], x_offset:x_offset + new_size[0]] = resized_img
        image = padded_image.copy()
        
    return image


def resize_mmseg_style(
    img: np.ndarray,
    scale: Tuple[int, int],
    *,
    keep_ratio: bool = True,
    interpolation: Literal["linear", "nearest", "area", "bicubic", "lanczos4"] = "linear",
    allow_upscale: bool = False,
) -> Tuple[np.ndarray, Tuple[float, float]]:
    """
    仿 mmsegmentation 的 Resize:
      dict(type='Resize', keep_ratio=True, scale=(W, H))

    参数:
        img: 输入图像 (H, W[, C])
        scale: (max_w, max_h) 目标缩放上限
        keep_ratio: 是否保持宽高比 (默认 True)
        interpolation: 插值方式 (图像通常 linear, mask 用 nearest)
        allow_upscale: 是否允许放大 (默认 False)

    返回:
        resized_img: 缩放后的图像
        (scale_factor_w, scale_factor_h): 实际缩放比例
    """
    if img is None or img.size == 0:
        raise ValueError("输入图像为空")

    max_w, max_h = scale
    h, w = img.shape[:2]

    interp_map = {
        "nearest": cv2.INTER_NEAREST,
        "linear": cv2.INTER_LINEAR,
        "area": cv2.INTER_AREA,
        "bicubic": cv2.INTER_CUBIC,
        "lanczos4": cv2.INTER_LANCZOS4,
    }
    interp = interp_map[interpolation]

    if keep_ratio:
        # 计算保持比例的缩放因子
        scale_w = max_w / w
        scale_h = max_h / h
        factor = min(scale_w, scale_h)
        if not allow_upscale:
            factor = min(factor, 1.0)
        new_w, new_h = int(round(w * factor)), int(round(h * factor))
    else:
        # 直接缩放到目标尺寸
        new_w, new_h = max_w, max_h

    resized = cv2.resize(img, (new_w, new_h), interpolation=interp)
    return resized, (new_w / w, new_h / h)


def resize_image_data(image_data, max_size=700):
    if image_data is None:
        logger.warning("图像数据为空， 未进行resize处理！")
        return None, None
    
    image = Image.open(io.BytesIO(image_data))
    width, height = image.size
    
    if image.mode == 'RGBA':
        image = image.convert('RGB')
        
    if width > max_size or height > max_size:
        if width > height:
            new_width = max_size
            new_height = int(height * (max_size / width))
        else:
            new_height = max_size
            new_width = int(width * (max_size / height))
        
        image = image.resize((new_width, new_height))
        width = new_width
        height = new_height
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        image_data = buffered.getvalue()
    return image_data, (width, height)



def draw_cn_text(img, text, pos=(10, 10), font_size=30):
    """
    在opencv图像上绘制中文
    img: cv2 BGR
    text: 要写的文字
    """

    # cv2 -> PIL
    img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)

    # 中文字体（Linux / Windows常见）
    font_path = "config/fonts/SourceHanSansCN-VF.ttf"
    if not os.path.exists(font_path):
        raise FileNotFoundError(f"字体文件 {font_path} 不存在！")

    font = ImageFont.truetype(font_path, font_size)

    draw.text(pos, text, font=font, fill=(255, 255, 0))

    # PIL -> cv2
    img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    return img
