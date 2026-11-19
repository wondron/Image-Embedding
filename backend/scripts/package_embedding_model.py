"""把导出的 embedding 模型放进它自己的、以内容命名的文件夹，供镜像使用。

    config/model/embedding/embeddingcls-<12 位十六进制>/<.onnx 文件>  +  <它引用的外部数据>

这 12 个十六进制字符是 API 为这份权重记录的 SHA-256 的开头（app.clients.inference
.artifact_hash：单个文件的哈希，或者排序后各文件哈希的总哈希），所以文件夹名总能说明
里面是哪份权重，两个版本也永远不会共用一个路径。文件保留原名：.onnx 里写着它的外部数据
文件名，重命名那个文件就会改变 .onnx 本身。

在不挂载数据卷的情况下更换模型（两个版本都打进镜像）：
  1. 在这里打包新的导出，构建镜像时旧的文件夹仍然保留在里面；
  2. 把 EMBEDDING_CONFIG.model_path 指向打印出的路径，部署；
  3. 「模型版本」：登记、生成向量、切换；需要时切回；
  4. 旧版本下线之后，下一个镜像就可以不带它的文件夹了。
切换之前，搜索一直使用旧版本自己文件夹里的权重。

--move 移动文件而不是复制（权重只保留一份，例如把正在使用的模型整理成这种布局）；
在新位置会重新校验哈希。

用法（在 backend/ 下）：
  python scripts/package_embedding_model.py /path/to/export/multitask_cls.onnx
  python scripts/package_embedding_model.py config/model/dino/multitask_cls.onnx --move
  python scripts/package_embedding_model.py model.onnx --dest config/model/embedding --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.clients.inference import artifact_hash, model_files  # noqa: E402

PREFIX = "embeddingcls-"
HASH_CHARS = 12


def package(source: Path, dest_root: Path, *, dry_run: bool = False, move: bool = False) -> Path:
    """把模型的文件复制（或移动）到 dest_root/embeddingcls-<hash>/；返回那里的 .onnx。
    同名文件夹已经存在时，里面必须是同样的文件（这时不复制；如果是移动，会删除源文件，
    只留下一份）。
    """
    files = model_files(str(source))
    want = {f["name"]: f["sha256"] for f in files}
    artifact = artifact_hash(files)
    folder = dest_root / f"{PREFIX}{artifact[:HASH_CHARS]}"
    target = folder / source.name
    print(f"weights      {source}  ({len(files)} file(s), {sum(f['size'] for f in files) / 2**20:.0f} MiB)")
    print(f"artifact     {artifact}")
    print(f"folder       {folder}")
    if folder.exists():
        have = {f["name"]: f["sha256"] for f in model_files(str(target))} if target.exists() else {}
        if have != want:
            raise SystemExit(f"{folder} exists with other files; remove it or check the export")
        if move and not dry_run and source.resolve() != target.resolve():
            for entry in files:
                os.remove(source.parent / entry["name"])
            print("already packaged: the source files were removed (one copy left)")
        else:
            print("already packaged: nothing copied")
        return target
    if dry_run:
        print(f"dry run: nothing {'moved' if move else 'copied'}")
        return target
    tmp = folder.with_name(folder.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    moved: list[str] = []
    try:
        for entry in files:
            if move:
                os.replace(source.parent / entry["name"], tmp / entry["name"])
                moved.append(entry["name"])
            else:
                shutil.copy2(source.parent / entry["name"], tmp / entry["name"])
        placed = {f["name"]: f["sha256"] for f in model_files(str(tmp / source.name))}
        if placed != want:
            raise SystemExit(f"the {'moved' if move else 'copied'} files do not match the source; nothing was packaged")
    except BaseException:
        for name in moved:  # 把移动过的文件放回原处
            os.replace(tmp / name, source.parent / name)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    os.replace(tmp, folder)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("onnx", type=Path, help="the exported .onnx (its external data next to it)")
    parser.add_argument("--dest", type=Path, default=BACKEND_ROOT / "config/model/embedding", help="where the version folders live")
    parser.add_argument("--dry-run", action="store_true", help="hash and print, copy nothing")
    parser.add_argument("--move", action="store_true", help="move the files instead of copying them (one copy left)")
    args = parser.parse_args(argv)
    if not args.onnx.is_file():
        parser.error(f"{args.onnx} is not a file")
    target = package(args.onnx.resolve(), args.dest.resolve(), dry_run=args.dry_run, move=args.move)
    try:
        shown = target.relative_to(BACKEND_ROOT).as_posix()
    except ValueError:
        shown = target.as_posix()
    print(f"\nEMBEDDING_CONFIG.model_path: {shown}")
    print("Keep the old version's folder in the image until that version is retired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
