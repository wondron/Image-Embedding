"""Put an exported embedding model into its own content-named folder for the image.

    config/model/embedding/embeddingcls-<12 hex>/<the .onnx>  +  <the external data it names>

The 12 hex characters start the SHA-256 the API records for these weights (app.clients.inference
.artifact_hash: one file's hash, or the hash over the sorted file hashes), so a folder name
always says which weights it holds, and two versions never share a path. The files keep their
names: the .onnx names its external data file, renaming that would change the .onnx itself.

Changing the model with no volume mounted (both versions travel in the image):
  1. package the new export here, build the image with the old folder still in it;
  2. point EMBEDDING_CONFIG.model_path at the printed path, deploy;
  3. 「模型版本」: register it, build its vectors, switch; switch back if needed;
  4. after retiring the old version, the next image may leave its folder out.
Search keeps using the old version's weights from its own folder until the switch.

--move moves the files instead of copying them (one copy of the weights, e.g. to put the model
already in use into this layout); the hashes are checked again at the new place.

Usage (from backend/):
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
    """Copy (or move) the model's files into dest_root/embeddingcls-<hash>/; returns the .onnx
    there. An existing folder of that name must hold the same files (then nothing is copied;
    with move, the source files are removed so one copy is left)."""
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
        for name in moved:  # put moved files back where they were
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
