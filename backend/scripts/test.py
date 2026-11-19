
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import stat
import shutil
import argparse
from pathlib import Path
from tqdm import tqdm


# 默认跳过的系统虚拟目录
DEFAULT_EXCLUDES = {
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/data",
}


def format_size(size):
    """格式化文件大小"""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{size:.2f} PB"


def scan_disk(root, threshold, top, excludes, one_file_system=False):
    """
    扫描磁盘：
    1. 统计叶子文件夹大小
    2. 找出超过指定大小的文件
    """

    root = os.path.abspath(root)

    if not os.path.isdir(root):
        print(f"[错误] 目录不存在: {root}")
        return

    root_dev = os.stat(root).st_dev

    # 每个目录累计的磁盘实际占用
    dir_sizes = {}

    # 是否为叶子目录
    dir_is_leaf = {}

    # 超大文件
    large_files = []

    # 扫描统计
    file_count = 0
    dir_count = 0
    error_count = 0
    errors = []

    def on_error(exc):
        nonlocal error_count
        error_count += 1
        if len(errors) < 20:
            errors.append(str(exc))

    print("=" * 75)
    print("                    Linux 磁盘空间扫描")
    print("=" * 75)
    print(f"扫描路径: {root}")
    print(f"大文件阈值: {format_size(threshold)}")
    print(f"单文件系统: {one_file_system}")
    print("=" * 75)

    with tqdm(
        desc="扫描进度",
        unit="项",
        dynamic_ncols=True,
    ) as pbar:

        for current, dirs, files in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=on_error,
        ):

            current = os.path.abspath(current)

            dir_count += 1
            dir_sizes.setdefault(current, 0)

            # 过滤目录
            kept_dirs = []
            has_subdir = False

            for dirname in dirs:
                child = os.path.abspath(
                    os.path.join(current, dirname)
                )

                try:
                    st = os.stat(child, follow_symlinks=False)

                    # 不跟随符号链接目录
                    if not stat.S_ISDIR(st.st_mode):
                        continue

                    has_subdir = True

                    # 跳过排除目录
                    if child in excludes:
                        continue

                    # 可选：不跨越文件系统
                    if one_file_system and st.st_dev != root_dev:
                        continue

                    kept_dirs.append(dirname)

                except OSError as exc:
                    error_count += 1
                    if len(errors) < 20:
                        errors.append(f"{child}: {exc}")

            dirs[:] = kept_dirs
            dir_is_leaf[current] = not has_subdir

            pbar.update(1)

            # 扫描当前目录下的文件
            for filename in files:

                file_path = os.path.join(current, filename)
                file_count += 1

                try:
                    st = os.stat(
                        file_path,
                        follow_symlinks=False,
                    )

                    # 只统计普通文件
                    if not stat.S_ISREG(st.st_mode):
                        pbar.update(1)
                        continue

                    # 文件逻辑大小
                    logical_size = st.st_size

                    # 实际占用磁盘空间
                    allocated_size = (
                        st.st_blocks * 512
                        if hasattr(st, "st_blocks")
                        else logical_size
                    )

                    # 累加当前目录大小
                    dir_sizes[current] += allocated_size

                    # 查找超过阈值的大文件
                    if max(logical_size, allocated_size) >= threshold:
                        large_files.append({
                            "path": file_path,
                            "size": logical_size,
                            "allocated": allocated_size,
                        })

                except OSError as exc:
                    error_count += 1
                    if len(errors) < 20:
                        errors.append(f"{file_path}: {exc}")

                pbar.update(1)

                if pbar.n % 1000 == 0:
                    pbar.set_postfix(
                        dirs=dir_count,
                        files=file_count,
                        errors=error_count,
                    )

    # 自底向上汇总目录大小
    sorted_dirs = sorted(
        dir_sizes.keys(),
        key=lambda p: len(Path(p).parts),
        reverse=True,
    )

    for path in sorted_dirs:
        parent = os.path.dirname(path)

        if parent in dir_sizes:
            dir_sizes[parent] += dir_sizes[path]

    # 提取叶子文件夹
    leaf_dirs = [
        (path, size)
        for path, size in dir_sizes.items()
        if dir_is_leaf.get(path, False) and size > 0
    ]

    leaf_dirs.sort(
        key=lambda x: x[1],
        reverse=True,
    )

    # 大文件排序
    large_files.sort(
        key=lambda x: max(x["size"], x["allocated"]),
        reverse=True,
    )

    # 输出磁盘基本信息
    usage = shutil.disk_usage(root)

    print("\n" + "=" * 75)
    print("一、磁盘空间")
    print("=" * 75)

    print(f"磁盘总容量: {format_size(usage.total)}")
    print(f"已使用空间: {format_size(usage.used)}")
    print(f"剩余空间:   {format_size(usage.free)}")
    print(f"使用率:     {usage.used / usage.total * 100:.2f}%")

    # 输出叶子目录
    print("\n" + "=" * 75)
    print(f"二、叶子文件夹 TOP {top if top > 0 else '全部'}")
    print("=" * 75)

    print(f"共发现 {len(leaf_dirs)} 个非空叶子文件夹\n")

    display_dirs = leaf_dirs[:top] if top > 0 else leaf_dirs

    for index, (path, size) in enumerate(display_dirs, 1):
        print(
            f"{index:4d}. "
            f"{format_size(size):>12s}  "
            f"{path}"
        )

    # 输出大文件
    print("\n" + "=" * 75)
    print(f"三、大于 {format_size(threshold)} 的文件")
    print("=" * 75)

    print(f"共发现 {len(large_files)} 个大文件\n")

    for index, item in enumerate(large_files, 1):
        print(
            f"{index:4d}. "
            f"文件大小: {format_size(item['size']):>12s} | "
            f"实际占用: {format_size(item['allocated']):>12s}\n"
            f"      路径: {item['path']}"
        )

    # 错误统计
    print("\n" + "=" * 75)
    print("四、扫描统计")
    print("=" * 75)

    print(f"扫描目录: {dir_count}")
    print(f"扫描文件: {file_count}")
    print(f"访问异常: {error_count}")

    if errors:
        print("\n部分访问异常:")
        for error in errors:
            print(f"  - {error}")

    print("\n扫描完成！")


def main():
    parser = argparse.ArgumentParser(
        description="Linux 磁盘空间扫描工具"
    )

    parser.add_argument(
        "root",
        nargs="?",
        default="/",
        help="扫描起始目录，默认 /",
    )

    parser.add_argument(
        "--threshold-gb",
        type=float,
        default=5,
        help="大文件阈值，默认 5GB",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=100,
        help="显示最大的叶子文件夹数量，0 表示全部",
    )

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="额外排除的目录，可重复指定",
    )

    parser.add_argument(
        "--one-file-system",
        action="store_true",
        help="只扫描起始目录所在的文件系统",
    )

    args = parser.parse_args()

    if args.threshold_gb <= 0 or args.top < 0:
        parser.error("阈值必须大于 0，top 不能小于 0")

    excludes = {
        os.path.abspath(p)
        for p in DEFAULT_EXCLUDES
    }

    excludes.update(
        os.path.abspath(p)
        for p in args.exclude
    )

    scan_disk(
        root=args.root,
        threshold=int(args.threshold_gb * 1024 ** 3),
        top=args.top,
        excludes=excludes,
        one_file_system=args.one_file_system,
    )


if __name__ == "__main__":
    main()
