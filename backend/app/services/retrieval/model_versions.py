"""模型版本（embedding_models，契约 §2.10）：哪个编码器写哪个 collection、新版本叫什么，
以及每种状态允许哪些操作。

    building  已登记；它的 collection 正在补齐（POST /api/embedding-models/{id}/build）
    ready     上一次构建结束时已经完整，或者是上一次切换之前的当前版本（为回滚保留）
    current   搜索和入库使用的版本（恰好一个）
    retired   已停用：它的 collection 已删除，台账行也已删除

版本由它的编码器指纹识别（clients.inference.Embedder.fingerprint）：预处理参数、权重文件
的 SHA-256，以及一张固定测试图的向量。三者都一致时，两个编码器写入同一个 embedding
空间：预处理相同、文件哈希相同（两个指纹都记录了哈希时；不比较文件名和路径，所以移动或
重命名文件仍是同一个模型），并且测试向量的相似度不低于 SAME_MODEL_MIN_COSINE。哈希能区分
任意两份权重，不管微调多轻微；测试向量检查同一份权重在这里算出的结果仍然相同。在生产编码器
（dinov3，1024 维）上实测：GPU 对 CPU 为 0.9999998；同样的权重把 cubic 缩放换成 linear
为 0.99907。
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.core import errors as err
from app.core.enums import Distance, ModelStatus

SAME_MODEL_MIN_COSINE = 0.9999
# 会改变 embedding 空间的指纹 spec 字段（model_path 不会）
SPACE_KEYS = ("output_name", "scale", "mean", "std", "img_interp")

LABEL_MAX_LEN = 64
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_COLLECTION_UNSAFE = re.compile(r"[^a-z0-9_]+")
COLLECTION_MAX_LEN = 120

# 没有指纹的当前版本，在把启动时用的编码器记录为它自己的编码器之前，用多少张已存
# 图片来核对
ADOPTION_SAMPLES = 3


def probe_of(fingerprint: Any) -> list[float] | None:
    """格式正确的指纹里的测试向量，否则为 None。"""
    if not isinstance(fingerprint, Mapping):
        return None
    probe = fingerprint.get("probe")
    if not isinstance(probe, Sequence) or isinstance(probe, (str, bytes)) or not probe:
        return None
    try:
        values = [float(v) for v in probe]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in values) or not isinstance(fingerprint.get("spec"), Mapping):
        return None
    return values


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；长度不同或长度为 0 的向量为 0.0。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


def _space_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(round(float(v), 9) if isinstance(v, float) else v for v in value)
    if isinstance(value, str):
        return value.strip().lower()
    return value


def file_hashes(fingerprint: Any) -> list[str] | None:
    """指纹记录的权重文件 SHA-256（排好序），否则为 None。"""
    if not isinstance(fingerprint, Mapping):
        return None
    files = fingerprint.get("files")
    if not isinstance(files, list) or not files:
        return None
    hashes = [f.get("sha256") for f in files if isinstance(f, Mapping)]
    if len(hashes) != len(files) or not all(isinstance(h, str) and h for h in hashes):
        return None
    return sorted(hashes)


def artifact_of(fingerprint: Any) -> str | None:
    """一个版本所有权重文件的总哈希（clients.inference.artifact_hash），否则为 None。"""
    value = fingerprint.get("artifact") if isinstance(fingerprint, Mapping) else None
    return value if isinstance(value, str) and value else None


def same_encoder(a: Any, b: Any) -> bool:
    """两个指纹描述的编码器写入同一个 embedding 空间时为 True。"""
    pa, pb = probe_of(a), probe_of(b)
    if pa is None or pb is None or len(pa) != len(pb):
        return False
    sa, sb = a["spec"], b["spec"]
    if any(_space_value(sa.get(key)) != _space_value(sb.get(key)) for key in SPACE_KEYS):
        return False
    ha, hb = file_hashes(a), file_hashes(b)
    if ha is not None and hb is not None and ha != hb:
        return False  # 不同的权重，不管它们在测试图上的输出多接近
    return cosine(pa, pb) >= SAME_MODEL_MIN_COSINE


def moved(recorded: Any, current: Any) -> bool:
    """在另一个路径下找到的同一个编码器（same_encoder），或者带着记录里没有的文件哈希：
    记录应该跟着更新，这样以后加载时能找到权重。
    """
    if not same_encoder(recorded, current):
        return False
    path = lambda fp: str(fp["spec"].get("model_path") or "")  # noqa: E731
    return path(recorded) != path(current) or (file_hashes(recorded) is None and file_hashes(current) is not None)


def fingerprint_dim(fingerprint: Any) -> int | None:
    probe = probe_of(fingerprint)
    return len(probe) if probe is not None else None


# -- 登记 ---------------------------------------------------------------------------


def label(value: Any, field: str) -> str:
    """版本的名称或版本标签：字母、数字、'.'、'_'、'-'（最多 64 个字符）。"""
    text = str(value or "").strip()
    if not _LABEL.match(text):
        raise err.validation_error(
            f"{field} must be 1-{LABEL_MAX_LEN} letters, digits, '.', '_' or '-' starting with a letter or digit, "
            f"got {value!r}"
        )
    return text


def default_name(model_path: str) -> str:
    """权重文件不带扩展名的文件名，作为标签（例如 multitask_cls）。"""
    stem = os.path.splitext(os.path.basename(str(model_path or "")))[0]
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:LABEL_MAX_LEN]
    return text or "model"


def default_version(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M")


def collection_name(name: str, version: str, taken: set[str]) -> str:
    """由小写字母、数字和 '_' 组成的 {name}_{version}（Qdrant 安全），名字已被占用时
    （被某个版本的行或 Qdrant 里的某个 collection 占用）加上 _2、_3……
    """
    base = _COLLECTION_UNSAFE.sub("_", f"{name}_{version}".lower()).strip("_")[:COLLECTION_MAX_LEN] or "model"
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}_{n}"
        if candidate not in taken:
            return candidate
    raise err.embedding_model_exists(f"no free collection name for {base!r}")


def registration_fields(*, name: str, version: str, collection: str, fingerprint: Mapping[str, Any]) -> dict[str, Any]:
    """由带这个指纹的编码器写入的新版本的 embedding_models 行。"""
    spec = dict(fingerprint["spec"])
    scale = [int(v) for v in spec.get("scale") or (224, 224)]
    return {
        "name": name,
        "version": version,
        "dim": len(fingerprint["probe"]),
        "distance": Distance.COSINE.value,  # 编码器会对向量做 L2 归一化
        "input_size": max(scale),
        "preprocess": {key: spec.get(key) for key in SPACE_KEYS},
        "weights_uri": str(spec.get("model_path") or ""),
        "collection": collection,
        "fingerprint": dict(fingerprint),
    }


# -- 每种状态允许什么 ---------------------------------------------------------------


def require_buildable(row: Mapping[str, Any]) -> None:
    if row["status"] == ModelStatus.RETIRED.value:
        raise err.embedding_model_state(f"embedding model {row['id']} is retired; register the encoder again")


def require_activatable(row: Mapping[str, Any], *, backlog: int, allow_missing: bool, reasons: str | None = None) -> None:
    """版本只要没下线就可以成为当前版本；它没有向量的图片在切换后会搜不到，所以必须显式
    允许。reasons：切换自带的补齐为什么没能为它们计算 embedding（失败的错误码）。
    """
    if row["status"] == ModelStatus.RETIRED.value:
        raise err.embedding_model_state(f"embedding model {row['id']} is retired")
    if backlog > 0 and not allow_missing:
        why = f" ({reasons})" if reasons else ""
        raise err.embedding_model_incomplete(
            f"{backlog} stored images have no vector in {row['collection']}{why}; finish its build first, "
            "or pass allow_missing=true (search cannot find them until they are embedded)"
        )


def require_retirable(row: Mapping[str, Any]) -> None:
    if row["status"] == ModelStatus.CURRENT.value:
        raise err.embedding_model_state("the current model version cannot be retired; switch to another first")


# -- 接管在有指纹之前登记的版本 -------------------------------------------------------


def check_collection_encoder(
    *,
    samples: list[Mapping[str, Any]],
    stored: Mapping[str, Sequence[float]],
    read_original: Any,
    embed: Any,
) -> dict[str, Any]:
    """对几张已存原图重新计算 embedding，和它们在 collection 里的向量比较。

    samples：[{id, original_url}]；stored：{id: collection 里的向量}；read_original(url) ->
    bytes；embed(bytes) -> vector。返回 {checked, min_similarity, matches}：什么都没能检查时
    （没有样本、原图读不了）matches 为 None，否则表示每个检查过的向量是否都达到
    SAME_MODEL_MIN_COSINE。不会因为单个样本而抛出。
    """
    similarities: list[float] = []
    for sample in samples:
        vector = stored.get(str(sample["id"]))
        if vector is None or not sample.get("original_url"):
            continue
        try:
            fresh = embed(read_original(str(sample["original_url"])))
        except Exception:
            continue
        similarities.append(cosine(list(fresh), list(vector)))
    if not similarities:
        return {"checked": 0, "min_similarity": None, "matches": None}
    lowest = min(similarities)
    return {"checked": len(similarities), "min_similarity": lowest, "matches": lowest >= SAME_MODEL_MIN_COSINE}
