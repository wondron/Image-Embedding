from __future__ import annotations

import os
import re
from datetime import datetime, date
from typing import Optional

import cv2
import numpy as np
import z_tools
from pypinyin import lazy_pinyin, Style


# =========================
# 图像读写（兼容中文路径）
# =========================
def imread_unicode(path: str):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_jpg_unicode(path: str, img: np.ndarray, quality: int = 95) -> bool:
    """
    保存为 jpg，不改变图像尺寸
    """
    try:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return False
        buf.tofile(path)
        return True
    except Exception:
        return False

def has_chinese(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fa5]", s))

# =========================
# 日期提取
# =========================
def _build_yyMMdd(year: int, month: int, day: int) -> Optional[str]:
    try:
        date(year, month, day)
    except ValueError:
        return None

    yy = year % 100
    return f"{yy:02d}{month:02d}{day:02d}"


def _normalize_year(year_str: str) -> int:
    y = int(year_str)
    if len(year_str) == 4:
        return y
    return 2000 + y if y < 70 else 1900 + y


def extract_date_yyMMdd(filename: str) -> Optional[str]:
    if not filename or not isinstance(filename, str):
        return None

    text = filename

    patterns = [
        re.compile(
            r'(?<!\d)'
            r'(?P<year>\d{4})'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'\d{4}'
            r'(?!\d)'
        ),
        re.compile(
            r'(?<!\d)'
            r'(?P<year>\d{4})'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'(?P<hour>[01]\d|2[0-3])'
            r'(?P<minute>[0-5]\d)'
            r'(?P<second>[0-5]\d)'
            r'(?!\d)'
        ),
        re.compile(
            r'(?<!\d)'
            r'(?P<year>\d{2})'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'(?P<hour>[01]\d|2[0-3])'
            r'(?P<minute>[0-5]\d)'
            r'(?P<second>[0-5]\d)'
            r'(?!\d)'
        ),
        re.compile(
            r'(?P<year>\d{4})[年/\-_. ]+'
            r'(?P<month>0[1-9]|1[0-2])[月/\-_. ]+'
            r'(?P<day>0[1-9]|[12]\d|3[01])日?'
            r'(?:[ T时:\-_.]+'
            r'(?P<hour>[01]\d|2[0-3])[时:\-_. ]+'
            r'(?P<minute>[0-5]\d)[分:\-_. ]+'
            r'(?P<second>[0-5]\d)秒?)?'
        ),
        re.compile(
            r'(?P<year>\d{2})[年/\-_. ]+'
            r'(?P<month>0[1-9]|1[0-2])[月/\-_. ]+'
            r'(?P<day>0[1-9]|[12]\d|3[01])日?'
            r'(?:[ T时:\-_.]+'
            r'(?P<hour>[01]\d|2[0-3])[时:\-_. ]+'
            r'(?P<minute>[0-5]\d)[分:\-_. ]+'
            r'(?P<second>[0-5]\d)秒?)?'
        ),
        re.compile(
            r'(?<!\d)'
            r'(?P<year>\d{4})'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'(?!\d)'
        ),
        re.compile(
            r'(?<!\d)'
            r'(?P<year>\d{2})'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'(?!\d)'
        ),
        re.compile(
            r'(?P<year>\d{4})[年/\-_. ]+'
            r'(?P<month>0[1-9]|1[0-2])[月/\-_. ]+'
            r'(?P<day>0[1-9]|[12]\d|3[01])日?'
        ),
        re.compile(
            r'(?P<year>\d{2})[年/\-_. ]+'
            r'(?P<month>0[1-9]|1[0-2])[月/\-_. ]+'
            r'(?P<day>0[1-9]|[12]\d|3[01])日?'
        ),
        re.compile(
            r'(?<!\d)'
            r'(?P<month>0[1-9]|1[0-2])'
            r'(?P<day>0[1-9]|[12]\d|3[01])'
            r'(?!\d)'
        ),
    ]

    for pat in patterns:
        m = pat.search(text)
        if not m:
            continue

        gd = m.groupdict()
        year_str = gd.get("year")
        month = int(gd["month"])
        day = int(gd["day"])

        if year_str is not None:
            year = _normalize_year(year_str)
        else:
            year = datetime.now().year

        result = _build_yyMMdd(year, month, day)
        if result:
            return result

    return None


# =========================
# 拼音缩写
# =========================
def _split_camel_part(pinyin_word: str):
    if not pinyin_word:
        return "", ""

    lower_word = pinyin_word.lower()
    if lower_word.startswith(("sh", "zh", "ch")):
        return pinyin_word[:2], pinyin_word[2:]
    return pinyin_word[:1], pinyin_word[1:]


def get_new_flag(dirname, max_len=10):
    if not dirname or not isinstance(dirname, str):
        return ""

    newstring = re.sub(r"\[[^\]]*\]", "", dirname)
    newstring = re.sub(r"\([^)]*\)", "", newstring)
    newstring = re.sub(r"[^\u4e00-\u9fa5]", "", newstring)

    if not newstring:
        return ""

    full_pinyin = lazy_pinyin(newstring, style=Style.NORMAL)
    if not full_pinyin:
        return ""

    first_word = full_pinyin[0].lower()

    if len(full_pinyin) == 1:
        return first_word[:max_len]

    first_parts = []
    second_parts = []

    for word in full_pinyin[1:]:
        camel_word = word.capitalize()
        p1, p2 = _split_camel_part(camel_word)
        first_parts.append(p1)
        second_parts.append(p2)

    result = first_word + "".join(first_parts)

    if len(result) > max_len:
        result = first_word + "".join(part[0] for part in first_parts)
        return result[:max_len]

    current = first_word
    built_parts = first_parts[:]

    for i in range(len(built_parts)):
        trial_parts = built_parts[:]
        trial_parts[i] = trial_parts[i] + second_parts[i]
        trial_result = current + "".join(trial_parts)

        if len(trial_result) <= max_len:
            built_parts = trial_parts
        else:
            break

    return current + "".join(built_parts)


# =========================
# 名称判定
# =========================
def contains_substring(s):
    if s.isdigit() and len(s) == 12:
        return True
    if re.fullmatch(r"[a-z0-9]{32}", s):
        return True

    substrs = [
        "Image_", "Bing_", "IMG_", "&", "副本", "ScreenShot", "Baidu_", 'VCG',
        ")", "=", "笋", "(", "小", "[", "（", "）", " ", "]", 'KEEP', "DROP",
        ".", "-", "?", "!", "file", "turbo", "shiCai",'avatar', '_C00', "_C01"
    ]
    for substr in substrs:
        if substr in s:
            return True

    if len(s) < 9:
        return True

    return False



def save_video_frames_every_n(
    video_path: str,
    output_dir: str,
    dir_flag: str,
    datestr: str,
    start_index: int = 0,
    frame_interval: int = 10,
    jpg_quality: int = 95,
    dry_run: bool = False,
):
    """
    每隔 frame_interval 帧截图一张，并按现有图片规则命名保存为 jpg

    返回:
        next_index: 下一个可用索引
        saved_count: 实际保存张数
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[FAILED] 视频打开失败：{video_path}")
        return start_index, 0

    frame_idx = 0
    save_count = 0
    index = start_index
    video_name = os.path.basename(video_path)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_idx % frame_interval == 0:
                target_path, index = build_unique_target_path(
                    output_dir,
                    dir_flag,
                    datestr,
                    start_index=index,
                )
                target_name = os.path.basename(target_path)

                if dry_run:
                    print(f"[DRYRUN][VIDEO_FRAME] {video_name} | 帧={frame_idx} -> {target_name}")
                    save_count += 1
                else:
                    ok_save = imwrite_jpg_unicode(target_path, frame, quality=jpg_quality)
                    if not ok_save:
                        print(f"[FAILED] 视频截图保存失败：{video_name} | 帧={frame_idx} -> {target_path}")
                    else:
                        print(f"[VIDEO_FRAME] {video_name} | 帧={frame_idx} -> {target_name}")
                        save_count += 1

                index += 1

            frame_idx += 1

    finally:
        cap.release()

    return index, save_count

# =========================
# 生成不重名的新文件名
# =========================
def build_unique_target_path(folder_path: str, dir_flag: str, datestr: str, start_index: int = 0) -> tuple[str, int]:
    index = start_index

    while True:
        new_name = f"{dir_flag}_{datestr}_{index:04d}.jpg"
        new_path = os.path.join(folder_path, new_name)
        if not os.path.exists(new_path):
            return new_path, index
        index += 1

def build_unique_keepname_path(folder_path: str, stem: str) -> str:
    """
    保留原文件名（仅改后缀为jpg），冲突时自动+1
    """
    base_path = os.path.join(folder_path, stem + ".jpg")

    if not os.path.exists(base_path):
        return base_path

    idx = 1
    while True:
        new_path = os.path.join(folder_path, f"{stem}_{idx}.jpg")
        if not os.path.exists(new_path):
            return new_path
        idx += 1

def build_unique_target_path(
    folder_path: str,
    dir_flag: str,
    datestr: str,
    start_index: int = 0
) -> tuple[str, int]:

    index = start_index

    while True:
        new_name = f"{dir_flag}_{datestr}_{index:04d}.jpg"
        new_path = os.path.join(folder_path, new_name)

        if not os.path.exists(new_path):
            return new_path, index

        index += 1

def is_special_png(filepath: str) -> bool:
    name = os.path.basename(filepath)
    stem = os.path.splitext(name)[0]
    ext = os.path.splitext(name)[1].lower()

    if ext != ".png":
        return False

    return stem.startswith(("200_", "300_", "400_"))

# =========================
# 处理单个文件夹
# =========================
def changes_names(folder_path, dry_run=False, jpg_quality: int = 95):
    print(f"\n====== 处理文件夹: {folder_path} ======")

    video_files = z_tools.get_all_video_paths(folder_path, recursive=False)
    print(f"发现视频数量: {len(video_files)}")
    video_idx = 0
    for filepath in video_files:
        video_name = os.path.basename(filepath)
        stem = os.path.splitext(video_name)[0]

        # ---------- 提取日期 ----------
        datestr = extract_date_yyMMdd(stem)
        if datestr is None:
            datestr = datetime.now().strftime('%y%m%d')
            print(f"视频未识别日期 → 使用当前日期: {datestr}")
        else:
            print(f"视频识别日期: {datestr}")

        # ---------- 找到真实目录名 ----------
        new_folder = os.path.dirname(filepath)
        while True:
            dirname = os.path.basename(new_folder)
            if '食材图' not in dirname:
                break
            parent = os.path.dirname(new_folder)
            if parent == new_folder:
                break
            new_folder = parent

        # ---------- 拼音 flag ----------
        dir_flag = get_new_flag(dirname)
        if not dir_flag:
            print(f"跳过视频（目录名无法生成拼音标识）：{filepath}")
            continue

        # ---------- dry run 模式下先预览 ----------
        if dry_run:
            print(f"[DRYRUN][VIDEO] 准备处理视频：{video_name}")
            next_index, saved_count = save_video_frames_every_n(
                video_path=filepath,
                output_dir=folder_path,
                dir_flag=dir_flag,
                datestr=datestr,
                start_index=video_idx,
                frame_interval=30,
                jpg_quality=jpg_quality,
                dry_run=True,
            )
            print(f"[DRYRUN][VIDEO] {video_name} 预计截图数量: {saved_count}")
            print(f"[DRYRUN][VIDEO] 预计删除原视频：{video_name}")
            video_idx = next_index
            continue

        # ---------- 真正执行截图 ----------
        next_index, saved_count = save_video_frames_every_n(
            video_path=filepath,
            output_dir=folder_path,
            dir_flag=dir_flag,
            datestr=datestr,
            start_index=video_idx,
            frame_interval=15,
            jpg_quality=jpg_quality,
            dry_run=False,
        )

        # ---------- 截到图后删除原视频 ----------
        if saved_count > 0:
            try:
                os.remove(filepath)
                print(f"[DELETE_VIDEO] 已删除原视频：{video_name}")
            except Exception as e:
                print(f"[WARN] 视频截图已完成，但删除原视频失败：{filepath} | {e}")
        else:
            print(f"[WARN] 视频未成功截出任何图片，保留原视频：{video_name}")

        video_idx = next_index



    image_files = z_tools.get_all_image_paths(folder_path, recursive=False)
    print(f"发现图片数量: {len(image_files)}")

    index = 0

    for filepath in image_files:
        image_name = os.path.basename(filepath)
        stem = os.path.splitext(image_name)[0]
        ext = os.path.splitext(image_name)[1].lower()

        # ---------- 特殊 PNG：200_ / 300_ / 400_ 开头 ----------
        # 规则：保留原名，仅转 jpg；若重名则加 _1 / _2 / _3 ...
        if is_special_png(filepath):
            target_path = build_unique_keepname_path(folder_path, stem)
            target_name = os.path.basename(target_path)

            if dry_run:
                print(f"[DRYRUN][SPECIAL_PNG] {image_name} -> {target_name}")
                continue

            img = imread_unicode(filepath)
            if img is None:
                print(f"[FAILED] 读取失败：{filepath}")
                continue

            ok = imwrite_jpg_unicode(target_path, img, quality=jpg_quality)
            if not ok:
                print(f"[FAILED] 保存 jpg 失败：{target_path}")
                continue

            try:
                os.remove(filepath)
            except Exception as e:
                print(f"[WARN] 新 jpg 已保存，但删除原文件失败：{filepath} | {e}")

            print(f"[SPECIAL_PNG] {image_name} -> {target_name}")
            continue

        # ---------- 判断是否需要处理 ----------
        # 非 jpg 一律处理
        # jpg 则按原规则：名称可疑 or 含中文 才处理
        if ext != ".jpg":
            need_process = True
        else:
            need_process = contains_substring(stem) or has_chinese(stem)

        if not need_process:
            continue

        # ---------- 提取日期 ----------
        datestr = extract_date_yyMMdd(stem)
        if datestr is None:
            datestr = datetime.now().strftime('%y%m%d')
            print(f"未识别日期 → 使用当前日期: {datestr}")
        else:
            print(f"识别日期: {datestr}")

        # ---------- 找到真实目录名 ----------
        new_folder = os.path.dirname(filepath)
        while True:
            dirname = os.path.basename(new_folder)
            if '食材图' not in dirname:
                break
            parent = os.path.dirname(new_folder)
            if parent == new_folder:
                break
            new_folder = parent

        # ---------- 拼音 flag ----------
        dir_flag = get_new_flag(dirname)
        if not dir_flag:
            print(f"跳过（目录名无法生成拼音标识）：{filepath}")
            continue

        # ---------- 生成不重名目标路径 ----------
        # 规则：
        # huanggua_260319_0000.jpg
        # huanggua_260319_0001.jpg
        # huanggua_260319_0002.jpg
        target_path, index = build_unique_target_path(
            folder_path,
            dir_flag,
            datestr,
            start_index=index
        )
        target_name = os.path.basename(target_path)

        if dry_run:
            action = "CONVERT+RENAME" if ext != ".jpg" else "RENAME"
            print(f"[DRYRUN][{action}] {image_name} -> {target_name}")
            index += 1
            continue

        # ---------- 原本就是 jpg：直接改名 ----------
        if ext == ".jpg":
            if os.path.abspath(filepath) == os.path.abspath(target_path):
                print(f"[SKIP] 文件名已符合目标：{image_name}")
            else:
                try:
                    os.rename(filepath, target_path)
                    print(f"[RENAME] {image_name} -> {target_name}")
                except Exception as e:
                    print(f"[FAILED] 改名失败：{filepath} -> {target_path} | {e}")
                    continue

            index += 1
            continue

        # ---------- 非 jpg：读取并转存为 jpg ----------
        img = imread_unicode(filepath)
        if img is None:
            print(f"[FAILED] 读取失败：{filepath}")
            continue

        ok = imwrite_jpg_unicode(target_path, img, quality=jpg_quality)
        if not ok:
            print(f"[FAILED] 保存 jpg 失败：{target_path}")
            continue

        try:
            os.remove(filepath)
        except Exception as e:
            print(f"[WARN] 新 jpg 已保存，但删除原文件失败：{filepath} | {e}")

        print(f"[CONVERT+RENAME] {image_name} -> {target_name}")
        index += 1

    print("\n====== 文件夹处理完成 ======")


if __name__ == "__main__":
    folder_path = r"C:\Users\240256\Desktop\一体机"
    dry_run = False   # True: 只预览不改名；False: 真正执行
    jpg_quality = 100

    image_folders = z_tools.list_folders(folder_path, recursive=True)
    image_folders.append(folder_path)

    for image_folder in image_folders:
        print(f"\n处理文件夹: {image_folder}")
        changes_names(image_folder, dry_run=dry_run, jpg_quality=jpg_quality)