import numpy as np
import cv2, configparser, os
import time, uuid, random
from PIL import Image
import io, re, shutil
from typing import Any, Dict, List, Sequence, Set, Tuple, Optional
from tqdm import tqdm
from pathlib import Path
from collections import Counter


class timer_tool:
    def __init__(self) -> None:
        self._last = time.perf_counter()
        self._buf: List[str] = []
        
    def mark(self, tag: str) -> None:
        now = time.perf_counter()
        self._buf.append(f"{tag} {(now - self._last):.3f}s   ")
        self._last = now

    def summary(self) -> str:
        return "".join(self._buf)


def resize_image_data(image_cv, max_size=700):
    h, w = image_cv.shape[:2]
    scale = min(max_size / max(h, w), 1.0)  # 不放大

    if scale == 1.0:
        return image_cv, 1.0

    new_w = int(w * scale)
    new_h = int(h * scale)

    img = cv2.resize(image_cv, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    return img, scale


def load_change_vocab(path: str):
    """
    读取标签替换规则。

    格式示例：
        青豆 豌豆
        红萝卜 胡萝卜
    """
    rules = []

    if not os.path.exists(path):
        print(f"标签替换文件不存在，跳过：{path}")
        return rules

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")

            if not line or line.lstrip().startswith("#"):
                continue

            parts = re.split(r"\s+", line.strip())

            if len(parts) == 1:
                src, dst = parts[0], ""
            else:
                src, dst = parts[0], parts[1]

            if src:
                rules.append((src, dst))

    return rules




def parse_tag_from_path(
    image_path: str,
    start_with="食材识别",
    change_rules=None,
) -> Optional[str]:
    """
    从路径解析标签，并应用标签替换规则。
    """
    not_detect = False

    def apply_change_vocab(text: str, rules) -> str:
        """
        顺序替换。
        注意：长词规则最好放前面，短词规则放后面。
        """
        for src, dst in rules:
            text = text.replace(src, dst)

        return text
    
    res = get_imagepath2clipText(image_path, start_with=start_with)

    if not res:
        return None

    pairs, _ = res

    if not pairs:
        return None

    tag = pairs[0][1]

    if not tag:
        return None

    if change_rules:
        tag = apply_change_vocab(tag, change_rules)

    if tag.endswith("N"):
        tag = tag[:-1].strip()
        not_detect = True

    tag = tag.strip()
    return tag or None, not_detect



def resize_image_data(image_data: bytes, max_size: int = 700):
    if image_data is None:
        return None, None

    image = Image.open(io.BytesIO(image_data))

    if image.mode == "RGBA":
        image = image.convert("RGB")
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    width, height = image.size
    scale = min(max_size / max(width, height), 1.0)

    if scale == 1.0:
        return image_data, (width, height)

    new_width = int(width * scale)
    new_height = int(height * scale)

    image = image.resize((new_width, new_height), Image.LANCZOS)

    buffered = io.BytesIO()
    image.save(buffered, format="JPEG", quality=95)
    return buffered.getvalue(), (new_width, new_height)


def count_all_file_exts(
    directory: str,
    recursive: bool = True,
    skip_dirs: Optional[Sequence[str]] = None,
    show_progress: bool = True,
    collect_non_jpg: bool = False,   # ✅ 是否收集非jpg路径
    max_non_jpg: Optional[int] = None,  # ✅ 最多收集多少条（防止爆内存）
    verbose: bool = True,
) -> Tuple[Counter, List[str]]:
    """
    高速统计目录下所有文件后缀数量，并可选收集非jpg路径。

    返回:
        counter: 后缀统计
        non_jpg_paths: 非jpg文件路径列表（可为空）
    """
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"路径不存在或不是文件夹: {directory}")

    skip_dirs = set(skip_dirs or [])
    counter = Counter()
    total_files = 0
    error_dirs = 0

    non_jpg_paths: List[str] = []

    def get_ext_fast(filename: str) -> str:
        name = filename.lower()
        pos = name.rfind(".")
        if pos <= 0:
            return "[无后缀]"
        return name[pos:]

    def is_jpg(filename: str) -> bool:
        name = filename.lower()
        return name.endswith(".jpg") or name.endswith(".jpeg")

    # ---------- 非递归 ----------
    if not recursive:
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total_files += 1
                            ext = get_ext_fast(entry.name)
                            counter[ext] += 1

                            # ✅ 收集非jpg
                            if collect_non_jpg and not is_jpg(entry.name):
                                if max_non_jpg is None or len(non_jpg_paths) < max_non_jpg:
                                    non_jpg_paths.append(entry.path)

                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            error_dirs += 1

    # ---------- 递归 ----------
    else:
        stack = [directory]
        pbar = tqdm(desc="扫描目录", ncols=100, unit="dir") if show_progress else None

        try:
            while stack:
                current_dir = stack.pop()
                if pbar is not None:
                    pbar.update(1)

                try:
                    with os.scandir(current_dir) as it:
                        for entry in it:
                            try:
                                if entry.is_file(follow_symlinks=False):
                                    total_files += 1
                                    ext = get_ext_fast(entry.name)
                                    counter[ext] += 1

                                    # ✅ 收集非jpg
                                    if collect_non_jpg and not is_jpg(entry.name):
                                        if max_non_jpg is None or len(non_jpg_paths) < max_non_jpg:
                                            non_jpg_paths.append(entry.path)

                                elif entry.is_dir(follow_symlinks=False):
                                    if entry.name not in skip_dirs:
                                        stack.append(entry.path)

                            except (PermissionError, FileNotFoundError, OSError):
                                continue

                except (PermissionError, FileNotFoundError, OSError):
                    error_dirs += 1
                    continue

                if pbar is not None:
                    pbar.set_postfix(
                        文件数=total_files,
                        后缀数=len(counter),
                        非jpg=len(non_jpg_paths) if collect_non_jpg else 0,
                    )

        finally:
            if pbar is not None:
                pbar.close()
    if verbose:
        print(f"总文件数: {total_files}")
        print(f"异常目录数: {error_dirs}")

        print("后缀统计 Top 30:")
        for ext, cnt in counter.most_common(30):
            print(f"{ext:12s} {cnt}")

        if collect_non_jpg:
            print(f"\n非jpg文件数量: {len(non_jpg_paths)}")

    return counter, non_jpg_paths

def get_all_image_paths(
    directory: str,
    filter_str: Optional[Sequence[str]] = None,
    ratio: float = 1.0,
    seed: Optional[int] = None,
    recursive: bool = True,
    skip_dirs: Optional[Sequence[str]] = None,
    prune_by_filter: bool = False,
    show_progress: bool = True,
) -> List[str]:
    """
    获取目录下所有图片路径，支持：
    1. 路径关键字过滤
    2. 按目录内图片比例随机抽样
    3. 指定随机种子
    4. 是否递归
    5. 跳过指定目录名
    6. 是否按 filter_str 进行目录剪枝
    7. tqdm 进度显示

    参数:
        directory: 根目录
        filter_str: 路径过滤关键词列表
        ratio: 每个目录内抽样比例，(0, 1]；>=1 时返回全部
        seed: 随机种子
        recursive: 是否递归子目录
        skip_dirs: 要跳过的目录名列表，如 ["duplicates", ".git"]
        prune_by_filter: True=当前目录不匹配就整棵子树跳过 ；False=继续扫描子目录，只过滤当前目录结果
        show_progress: 是否显示进度条

    返回:
        图片完整路径列表
    """
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"路径不存在或不是文件夹: {directory}")

    image_suffixes: Tuple[str, ...] = (
        ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"
    )
    filter_str = tuple(filter_str or [])
    skip_dirs = set(skip_dirs or [])
    rng = random.Random(seed)

    def path_match(root_path: str) -> bool:
        if not filter_str:
            return True
        return any(s in root_path for s in filter_str)

    def should_skip_dir(dir_name: str, full_path: str) -> bool:
        if dir_name in skip_dirs:
            return True
        return False

    def sample_images(images: List[str]) -> List[str]:
        n = len(images)
        if n == 0:
            return images
        if ratio >= 1.0:
            return images
        if ratio <= 0:
            return []
        num_to_select = max(1, int(n * ratio))
        if num_to_select >= n:
            return images
        return rng.sample(images, num_to_select)

    # ---------- 不递归 ----------
    if not recursive:
        if prune_by_filter and not path_match(directory):
            return []

        images_in_folder: List[str] = []
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith(image_suffixes):
                                images_in_folder.append(entry.path)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            return []

        if not path_match(directory):
            return []
        return sample_images(images_in_folder)

    # ---------- 递归 ----------
    image_paths: List[str] = []
    stack = [directory]

    pbar = tqdm(desc="扫描目录", ncols=100, unit="dir") if show_progress else None

    try:
        while stack:
            current_dir = stack.pop()
            if pbar is not None:
                pbar.update(1)

            current_match = path_match(current_dir)

            if prune_by_filter and not current_match:
                continue

            images_in_folder: List[str] = []

            try:
                with os.scandir(current_dir) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if current_match and entry.name.lower().endswith(image_suffixes):
                                    images_in_folder.append(entry.path)

                            elif entry.is_dir(follow_symlinks=False):
                                if not should_skip_dir(entry.name, entry.path):
                                    stack.append(entry.path)

                        except (PermissionError, FileNotFoundError, OSError):
                            continue

            except (PermissionError, FileNotFoundError, OSError):
                continue

            image_paths.extend(sample_images(images_in_folder))

            if pbar is not None:
                pbar.set_postfix(图片数=len(image_paths))

    finally:
        if pbar is not None:
            pbar.close()

    return image_paths


def get_all_video_paths(
    directory: str,
    filter_str: Optional[Sequence[str]] = None,
    ratio: float = 1.0,
    seed: Optional[int] = None,
    recursive: bool = True,
    skip_dirs: Optional[Sequence[str]] = None,
    prune_by_filter: bool = False,
    show_progress: bool = True,
) -> List[str]:
    """
    获取目录下所有视频路径，支持：
    1. 路径关键字过滤
    2. 按目录内视频比例随机抽样
    3. 指定随机种子
    4. 是否递归
    5. 跳过指定目录名
    6. 是否按 filter_str 进行目录剪枝
    7. tqdm 进度显示

    参数:
        directory: 根目录
        filter_str: 路径过滤关键词列表
        ratio: 每个目录内抽样比例，(0, 1]；>=1 时返回全部
        seed: 随机种子
        recursive: 是否递归子目录
        skip_dirs: 要跳过的目录名列表，如 ["duplicates", ".git"]
        prune_by_filter: True=当前目录不匹配就整棵子树跳过；False=继续扫描子目录，只过滤当前目录结果
        show_progress: 是否显示进度条

    返回:
        视频完整路径列表
    """
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"路径不存在或不是文件夹: {directory}")

    video_suffixes: Tuple[str, ...] = (
        ".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".mpeg", ".mpg", ".m4v", ".ts", ".webm"
    )
    filter_str = tuple(filter_str or [])
    skip_dirs = set(skip_dirs or [])
    rng = random.Random(seed)

    def path_match(root_path: str) -> bool:
        if not filter_str:
            return True
        return any(s in root_path for s in filter_str)

    def should_skip_dir(dir_name: str, full_path: str) -> bool:
        if dir_name in skip_dirs:
            return True
        return False

    def sample_videos(videos: List[str]) -> List[str]:
        n = len(videos)
        if n == 0:
            return videos
        if ratio >= 1.0:
            return videos
        if ratio <= 0:
            return []
        num_to_select = max(1, int(n * ratio))
        if num_to_select >= n:
            return videos
        return rng.sample(videos, num_to_select)

    # ---------- 不递归 ----------
    if not recursive:
        if prune_by_filter and not path_match(directory):
            return []

        videos_in_folder: List[str] = []
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith(video_suffixes):
                                videos_in_folder.append(entry.path)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            return []

        if not path_match(directory):
            return []
        return sample_videos(videos_in_folder)

    # ---------- 递归 ----------
    video_paths: List[str] = []
    stack = [directory]

    pbar = tqdm(desc="扫描目录", ncols=100, unit="dir") if show_progress else None

    try:
        while stack:
            current_dir = stack.pop()
            if pbar is not None:
                pbar.update(1)

            current_match = path_match(current_dir)

            if prune_by_filter and not current_match:
                continue

            videos_in_folder: List[str] = []

            try:
                with os.scandir(current_dir) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if current_match and entry.name.lower().endswith(video_suffixes):
                                    videos_in_folder.append(entry.path)

                            elif entry.is_dir(follow_symlinks=False):
                                if not should_skip_dir(entry.name, entry.path):
                                    stack.append(entry.path)

                        except (PermissionError, FileNotFoundError, OSError):
                            continue

            except (PermissionError, FileNotFoundError, OSError):
                continue

            video_paths.extend(sample_videos(videos_in_folder))

            if pbar is not None:
                pbar.set_postfix(视频数=len(video_paths))

    finally:
        if pbar is not None:
            pbar.close()

    return video_paths

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


def resize_image(image_data, max_size=700):
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
    

def get_leaf_directories(
    root_dir: str,
    skip_empty: bool = False,
    skip_duplicates: bool = True,
    skip_keywords: Optional[Sequence[str]] = None,
    keep_keywords: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    获取指定路径下所有叶子目录（最后一级文件夹），适合 NAS 路径扫描。

    参数：
        root_dir: 根目录
        skip_empty: 是否跳过空叶子目录
        skip_duplicates: 是否跳过路径中包含 duplicates 的目录
        skip_keywords: 若当前文件夹名称包含任一关键字，则跳过该目录
        keep_keywords: 若叶子目录名称包含任一关键字，则保留；为 None 时默认都保留

    返回：
        叶子目录完整路径列表
    """
    root = Path(root_dir)
    if not root.exists():
        print(f"路径不存在：{root_dir}")
        return []
    skip_keywords = tuple(str(keyword).lower() for keyword in (skip_keywords or []) if keyword)
    keep_keywords = None if keep_keywords is None else tuple(
        str(keyword).lower() for keyword in keep_keywords if keyword
    )

    def should_skip_path(path_str: str) -> bool:
        path_obj = Path(path_str)
        if skip_duplicates and "duplicates" in path_obj.parts:
            return True

        if skip_keywords and any(keyword in path_obj.name.lower() for keyword in skip_keywords):
            return True

        return False

    def should_keep_leaf(path_str: str) -> bool:
        if keep_keywords is None:
            return True

        folder_name = Path(path_str).name.lower()
        return any(keyword in folder_name for keyword in keep_keywords)

    leaf_dirs: List[str] = []
    stack: List[str] = [str(root)]
    print("扫描叶子目录中（NAS 优化版，scandir）...")
    with tqdm(desc="扫描目录", ncols=100, unit="dir") as pbar:
        while stack:
            cur = stack.pop()
            pbar.update(1)

            if should_skip_path(cur):
                continue

            try:
                has_subdir = False
                has_file = False
                subdirs = []

                with os.scandir(cur) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append(entry.path)
                                has_subdir = True
                            else:
                                has_file = True
                        except Exception:
                            continue

                if has_subdir:
                    stack.extend(subdirs)
                else:
                    if skip_empty and not has_file:
                        continue
                    if should_keep_leaf(cur):
                        leaf_dirs.append(cur)

                pbar.set_postfix(叶子目录=len(leaf_dirs))

            except Exception:
                continue

    print(f"完成，共 {len(leaf_dirs)} 个叶子目录\n")
    return leaf_dirs

def list_folders(path: str, recursive: bool = True, include_self: bool = False) -> List[str]:
    if not os.path.isdir(path):
        raise NotADirectoryError(f"路径不存在或不是文件夹: {path}")

    result = []
    if include_self:
        result.append(path)
    print("扫描目录（NAS优化）...")
    if not recursive:
        with tqdm(desc="扫描一级目录", ncols=100, unit="dir") as pbar:
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        pbar.update(1)
                        if entry.is_dir() and "duplicates" not in entry.name:
                            result.append(entry.path)
            except Exception:
                return []

    else:
        with tqdm(desc="扫描目录", ncols=100, unit="dir") as pbar:
            for root, dirs, _ in os.walk(path):
                # ✅ 剪枝 duplicates（关键优化）
                dirs[:] = [d for d in dirs if "duplicates" not in d]

                for d in dirs:
                    pbar.update(1)
                    result.append(os.path.join(root, d))

    print(f"完成，共 {len(result)} 个文件夹\n")
    return result


def get_imagepath2clipText(image_path: str, start_with="食材识别"):
    """
    从图片路径解析食材名称。

    兼容原始逻辑：
    1. 找到路径中的 start_with，例如“食材识别”
    2. 取它后面的路径
    3. 如果目录中有 Txxx，则优先取 Txxx 去掉 T 后作为标签
    4. 否则取倒数第 3 层作为标签
    """
    pos = image_path.find(start_with)
    if pos == -1:
        return None

    use_string = image_path[pos + len(start_with):]
    food_name = None

    dir_name_path = os.path.dirname(use_string)

    if "T" in dir_name_path:
        parts = [p for p in re.split(r"[\\/]+", dir_name_path) if p]

        for item in parts:
            if "T" in item:
                food_name = item.replace("T", "")
                break
    else:
        parts = [p for p in re.split(r"[\\/]+", use_string) if p]

        if len(parts) < 3:
            print(f"路径层级不足，无法解析标签：{image_path}")
            return None

        result = parts[:-2]

        if not result:
            print(f"路径层级异常，无法解析标签：{image_path}")
            return None

        food_name = result[-1]

    if not food_name:
        print(f"未解析到食材名称：{image_path}")
        return None

    return [(image_path, food_name)], [parts[:-2]]


def get_files_by_ext(
    directory: str,
    target_exts: Sequence[str],   # ✅ 指定格式
    recursive: bool = True,
    skip_dirs: Optional[Sequence[str]] = None,
    show_progress: bool = True,
) -> List[str]:
    """
    获取指定后缀的文件路径

    参数:
        directory: 根目录
        target_exts: 目标后缀，如 [".mp4", ".jpg"]
        recursive: 是否递归
        skip_dirs: 跳过目录
        show_progress: 是否显示进度

    返回:
        匹配文件路径列表
    """

    if not os.path.isdir(directory):
        raise NotADirectoryError(f"路径不存在或不是文件夹: {directory}")

    # 标准化后缀
    target_exts = tuple(e.lower() for e in target_exts)
    skip_dirs = set(skip_dirs or [])

    results: List[str] = []

    # ---------- 不递归 ----------
    if not recursive:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        if entry.name.lower().endswith(target_exts):
                            results.append(entry.path)
                except:
                    continue
        return results

    # ---------- 递归 ----------
    stack = [directory]
    pbar = tqdm(desc="扫描目录", ncols=100, unit="dir") if show_progress else None

    try:
        while stack:
            cur = stack.pop()
            if pbar is not None:
                pbar.update(1)

            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if entry.name.lower().endswith(target_exts):
                                    results.append(entry.path)

                            elif entry.is_dir(follow_symlinks=False):
                                if entry.name not in skip_dirs:
                                    stack.append(entry.path)

                        except:
                            continue
            except:
                continue

            if pbar is not None:
                pbar.set_postfix(命中=len(results))

    finally:
        if pbar is not None:
            pbar.close()

    return results




def copy_all_files(src_dir: Path, dst_dir: Path, overwite: bool = False):
    if not src_dir.exists():
        raise FileNotFoundError(f"源路径不存在: {src_dir}")

    if not src_dir.is_dir():
        raise NotADirectoryError(f"源路径不是文件夹: {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in src_dir.rglob("*") if p.is_file()]

    print(f"源路径: {src_dir}")
    print(f"目标路径: {dst_dir}")
    print(f"文件数量: {len(files)}")

    copied = 0
    skipped = 0
    failed = 0

    for src_file in tqdm(files, desc="复制文件"):
        try:
            # 计算相对路径，保证目录结构不变
            rel_path = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel_path

            # 创建目标文件所在目录
            dst_file.parent.mkdir(parents=True, exist_ok=True)

            if dst_file.exists() and not overwite:
                skipped += 1
                continue

            shutil.copy2(src_file, dst_file)
            copied += 1

        except Exception as e:
            failed += 1
            print(f"复制失败: {src_file} -> {e}")

    print("-" * 60)
    print(f"复制完成")
    print(f"成功复制: {copied}")
    print(f"跳过文件: {skipped}")
    print(f"失败文件: {failed}")




if __name__ == "__main__":
    import time
    detect_folder = r"F:\yunnan"
    start = time.time()
    mp4_files = get_files_by_ext(
        directory=detect_folder,
        target_exts=[".mp4", 'bmp']
    )
    a = count_all_file_exts(detect_folder)
    print(f"耗时：{time.time() - start:.2f}s")
    print(a)
    print(mp4_files)    