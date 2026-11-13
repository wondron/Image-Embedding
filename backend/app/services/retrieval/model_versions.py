"""Model versions (embedding_models, contract §2.10): which encoder writes which collection, what
a new version is called, and which action each status allows.

    building  registered; its collection is being filled (POST /api/embedding-models/{id}/build)
    ready     complete when its last build ended, or the version that was current before the last
              switch (kept for a roll back)
    current   the version search and index use (exactly one)
    retired   out of use: its collection was dropped and its ledger rows deleted

A version is identified by its encoder's fingerprint (clients.inference.Embedder.fingerprint):
the preprocessing, the SHA-256 of the weight files and the vector of one fixed test image. Two
encoders write the same embedding space when all three agree: equal preprocessing, equal file
hashes (when both fingerprints recorded them; names and paths are not compared, so moving or
renaming the files keeps the model) and test vectors within SAME_MODEL_MIN_COSINE. The hashes
tell any two weights apart, however light the fine-tune; the test vector checks that the same
weights still compute the same thing here. Measured on the production encoder (dinov3, 1024-d):
GPU against CPU 0.9999998; the same weights with linear instead of cubic resizing 0.99907.
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
# fingerprint spec fields that change the embedding space (model_path does not)
SPACE_KEYS = ("output_name", "scale", "mean", "std", "img_interp")

LABEL_MAX_LEN = 64
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_COLLECTION_UNSAFE = re.compile(r"[^a-z0-9_]+")
COLLECTION_MAX_LEN = 120

# How many stored images a fingerprint-less current version is checked against before the
# encoder that starts on it is recorded as its own
ADOPTION_SAMPLES = 3


def probe_of(fingerprint: Any) -> list[float] | None:
    """The test vector of a well-formed fingerprint, else None."""
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
    """Cosine similarity; 0.0 for vectors of different length or zero length."""
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
    """The sorted SHA-256 of the weight files a fingerprint recorded, else None."""
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
    """The one hash over a version's weight files (clients.inference.artifact_hash), else None."""
    value = fingerprint.get("artifact") if isinstance(fingerprint, Mapping) else None
    return value if isinstance(value, str) and value else None


def same_encoder(a: Any, b: Any) -> bool:
    """True when the two fingerprints describe encoders that write the same embedding space."""
    pa, pb = probe_of(a), probe_of(b)
    if pa is None or pb is None or len(pa) != len(pb):
        return False
    sa, sb = a["spec"], b["spec"]
    if any(_space_value(sa.get(key)) != _space_value(sb.get(key)) for key in SPACE_KEYS):
        return False
    ha, hb = file_hashes(a), file_hashes(b)
    if ha is not None and hb is not None and ha != hb:
        return False  # other weights, however close their output on the test image
    return cosine(pa, pb) >= SAME_MODEL_MIN_COSINE


def moved(recorded: Any, current: Any) -> bool:
    """The same encoder (same_encoder) found at another path, or with file hashes the record
    lacks: the record should follow it, so a later load finds the weights."""
    if not same_encoder(recorded, current):
        return False
    path = lambda fp: str(fp["spec"].get("model_path") or "")  # noqa: E731
    return path(recorded) != path(current) or (file_hashes(recorded) is None and file_hashes(current) is not None)


def fingerprint_dim(fingerprint: Any) -> int | None:
    probe = probe_of(fingerprint)
    return len(probe) if probe is not None else None


# -- registration -------------------------------------------------------------------


def label(value: Any, field: str) -> str:
    """A version's name or version label: letters, digits, '.', '_', '-' (at most 64)."""
    text = str(value or "").strip()
    if not _LABEL.match(text):
        raise err.validation_error(
            f"{field} must be 1-{LABEL_MAX_LEN} letters, digits, '.', '_' or '-' starting with a letter or digit, "
            f"got {value!r}"
        )
    return text


def default_name(model_path: str) -> str:
    """The weights file's name without extension, as a label (e.g. multitask_cls)."""
    stem = os.path.splitext(os.path.basename(str(model_path or "")))[0]
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:LABEL_MAX_LEN]
    return text or "model"


def default_version(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M")


def collection_name(name: str, version: str, taken: set[str]) -> str:
    """{name}_{version} in lowercase letters, digits and '_' (Qdrant-safe), with _2, _3... when
    the name is already taken (by a version's row or by a collection in Qdrant)."""
    base = _COLLECTION_UNSAFE.sub("_", f"{name}_{version}".lower()).strip("_")[:COLLECTION_MAX_LEN] or "model"
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}_{n}"
        if candidate not in taken:
            return candidate
    raise err.embedding_model_exists(f"no free collection name for {base!r}")


def registration_fields(*, name: str, version: str, collection: str, fingerprint: Mapping[str, Any]) -> dict[str, Any]:
    """The embedding_models row of a new version written by the encoder with this fingerprint."""
    spec = dict(fingerprint["spec"])
    scale = [int(v) for v in spec.get("scale") or (224, 224)]
    return {
        "name": name,
        "version": version,
        "dim": len(fingerprint["probe"]),
        "distance": Distance.COSINE.value,  # the encoder L2-normalizes its vectors
        "input_size": max(scale),
        "preprocess": {key: spec.get(key) for key in SPACE_KEYS},
        "weights_uri": str(spec.get("model_path") or ""),
        "collection": collection,
        "fingerprint": dict(fingerprint),
    }


# -- what each status allows ---------------------------------------------------------


def require_buildable(row: Mapping[str, Any]) -> None:
    if row["status"] == ModelStatus.RETIRED.value:
        raise err.embedding_model_state(f"embedding model {row['id']} is retired; register the encoder again")


def require_activatable(row: Mapping[str, Any], *, backlog: int, allow_missing: bool, reasons: str | None = None) -> None:
    """A version can become current unless retired; images it has no vector for cannot be found
    after the switch, so they must be allowed explicitly. reasons: why the switch's own top-up
    could not embed them (failure codes)."""
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


# -- adopting a version registered before fingerprints existed -------------------------


def check_collection_encoder(
    *,
    samples: list[Mapping[str, Any]],
    stored: Mapping[str, Sequence[float]],
    read_original: Any,
    embed: Any,
) -> dict[str, Any]:
    """Embed a few stored originals again and compare them with their vectors in the collection.

    samples: [{id, original_url}]; stored: {id: vector in the collection}; read_original(url) ->
    bytes; embed(bytes) -> vector. Returns {checked, min_similarity, matches}: matches is None
    when nothing could be checked (no sample, unreadable originals), else whether every checked
    vector agrees to SAME_MODEL_MIN_COSINE. Never raises for one sample.
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
