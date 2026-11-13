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

Usage (from backend/):
  python scripts/package_embedding_model.py /path/to/export/multitask_cls.onnx
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


def package(source: Path, dest_root: Path, *, dry_run: bool = False) -> Path:
    """Copy the model's files into dest_root/embeddingcls-<hash>/; returns the .onnx there.
    An existing folder of that name must hold the same files (then nothing is copied)."""
    files = model_files(str(source))
    artifact = artifact_hash(files)
    folder = dest_root / f"{PREFIX}{artifact[:HASH_CHARS]}"
    target = folder / source.name
    print(f"weights      {source}  ({len(files)} file(s), {sum(f['size'] for f in files) / 2**20:.0f} MiB)")
    print(f"artifact     {artifact}")
    print(f"folder       {folder}")
    if folder.exists():
        have = {f["name"]: f["sha256"] for f in model_files(str(target))} if target.exists() else {}
        want = {f["name"]: f["sha256"] for f in files}
        if have != want:
            raise SystemExit(f"{folder} exists with other files; remove it or check the export")
        print("already packaged: nothing copied")
        return target
    if dry_run:
        print("dry run: nothing copied")
        return target
    tmp = folder.with_name(folder.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for entry in files:
        shutil.copy2(source.parent / entry["name"], tmp / entry["name"])
    copied = {f["name"]: f["sha256"] for f in model_files(str(tmp / source.name))}
    if copied != {f["name"]: f["sha256"] for f in files}:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit("the copy does not match the source; nothing was packaged")
    os.replace(tmp, folder)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("onnx", type=Path, help="the exported .onnx (its external data next to it)")
    parser.add_argument("--dest", type=Path, default=BACKEND_ROOT / "config/model/embedding", help="where the version folders live")
    parser.add_argument("--dry-run", action="store_true", help="hash and print, copy nothing")
    args = parser.parse_args(argv)
    if not args.onnx.is_file():
        parser.error(f"{args.onnx} is not a file")
    target = package(args.onnx.resolve(), args.dest.resolve(), dry_run=args.dry_run)
    try:
        shown = target.relative_to(BACKEND_ROOT).as_posix()
    except ValueError:
        shown = target.as_posix()
    print(f"\nEMBEDDING_CONFIG.model_path: {shown}")
    print("Keep the old version's folder in the image until that version is retired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
