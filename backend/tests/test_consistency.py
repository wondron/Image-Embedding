"""数据核对（/api/consistency）：PostgreSQL、Qdrant、对象存储三边的差别，以及模型版本之间的差别。

Qdrant 用进程内的真实实例（scroll / retrieve / describe 是真的），PostgreSQL 和对象存储是内存里的
假实现，保持仓储的规则（SQL 在 test_pg_live 里）。
"""

import hashlib
import math
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import consistency as consistency_routes
from app.bootstrap.consistency import ConsistencyChecker
from app.bootstrap.state import ApplicationState
from app.clients.image_storage import MockImageStorage, S3ImageStorage
from app.clients.qdrant import QdrantConnection, QdrantStore
from app.core import errors as err
from app.core.config.settings import ImageStorageSettings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.core.rules.scene_membership import build_qdrant_payload
from app.repositories.vectors import PAYLOAD_INDEXES, QdrantVectorStore
from app.services.retrieval import consistency as cc
from app.services.retrieval.consistency import CheckOptions, ConsistencyCheck, VersionTarget, build_issues, overall_status

MEMORY = QdrantConnection(url=":memory:")
DIM = 4
SPEC = {"model_path": "config/model/dino/a.onnx", "output_name": "cls_emb", "scale": [224, 224],
        "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225], "img_interp": "cubic"}


def unit(values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


VEC_A = unit([0.9, 0.1, 0.3, 0.2])
VEC_B = unit([0.1, 0.9, 0.2, 0.4])


def image_id(n):
    return str(uuid.UUID(int=n))


def fingerprint(probe):
    return {"spec": dict(SPEC), "probe": list(probe)}


class FakeStore:
    """images + image_embeddings + image_deletions + embedding_models，按 id 顺序读。"""

    def __init__(self):
        self.images = {}
        self.ledger = {}  # (model_id, image_id) -> synced_version
        self.deletions = {}  # image_id -> {original_url, thumb_url}
        self.models = []
        self.hidden = set()  # 第一次 existing_image_ids 时还没提交的图片

    def add_image(self, n, *, version=1, category_id=None, data=None):
        iid = image_id(n)
        data = data if data is not None else f"image-{n}".encode()
        self.images[iid] = {
            "id": iid, "sha256": hashlib.sha256(data).hexdigest(), "payload_version": version,
            "original_url": f"https://cdn/original/{iid[:2]}/{iid}.jpg", "thumb_url": f"https://cdn/thumb/{iid[:2]}/{iid}.webp",
            "category_id": category_id, "label_status": "unlabeled", "food_state": None, "scenes": ["general"],
        }
        return iid

    def payload(self, iid):
        row = self.images[iid]
        return build_qdrant_payload(category_id=row["category_id"], scene_codes=row["scenes"],
                                    food_state=row["food_state"], label_status=row["label_status"])

    # -- PgConsistencyMixin
    def scan_images(self, *, after=None, limit=500):
        visible = (i for i in self.images if i not in self.hidden)  # 还没提交的读不到
        ids = sorted(i for i in visible if after is None or i > str(after))[:limit]
        return [{**self.images[i], "ledger": {m: v for (m, img), v in self.ledger.items() if img == i}} for i in ids]

    def existing_image_ids(self, ids):
        found = {i for i in ids if i in self.images and i not in self.hidden}
        self.hidden -= set(ids)  # 第二次再查时已经提交了
        return found

    def queued_deletion_ids(self, ids):
        return {i for i in ids if i in self.deletions}

    def file_refs(self, ids):
        out = {}
        for i in ids:
            if i in self.images and i not in self.hidden:
                row = self.images[i]
                out.setdefault(i, []).append({"original_url": row["original_url"], "thumb_url": row["thumb_url"], "deleted": False})
            if i in self.deletions:
                out.setdefault(i, []).append({**self.deletions[i], "deleted": True})
        return out

    def consistency_counts(self):
        return {"images": len(self.images), "pending_deletions": len(self.deletions),
                "oldest_deletion_at": "2026-11-26T08:00:00+00:00" if self.deletions else None,
                "current_models": sum(1 for m in self.models if m["status"] == "current")}

    # -- 其他用到的
    def sample_embedded_images(self, model_id, *, limit=3):
        ids = sorted(img for (m, img) in self.ledger if m == model_id)[:limit]
        return [{"id": i, "original_url": self.images[i]["original_url"]} for i in ids]

    def list_embedding_model_rows(self, *, statuses=None):
        return [dict(m) for m in self.models if statuses is None or m["status"] in statuses]

    def ping(self):
        return {"ok": True, "host": "pg", "database": "image_embedding"}


class FakeStorage:
    """对象 key -> 字节；有几个 key 读不了（无权访问）。"""

    def __init__(self):
        self.objects = {}
        self.forbidden = set()

    def put(self, url, data):
        self.objects[url.split("https://cdn/", 1)[-1]] = data

    def _key(self, url):
        return url.split("https://cdn/", 1)[-1]

    def inventory_unavailable(self):
        return None

    def head(self, url):
        key = self._key(url)
        if key in self.forbidden:
            raise err.image_file_unavailable(f"head_object bucket/{key}: ClientError: 403 Forbidden")
        data = self.objects.get(key)
        return None if data is None else len(data)

    def read_by_url_or_key(self, url):
        key = self._key(url)
        if key not in self.objects:
            raise err.image_file_unavailable(f"no {key}")
        return self.objects[key]

    def list_keys(self, directory):
        for key in sorted(self.objects):
            if key.startswith(directory + "/"):
                yield key, len(self.objects[key])

    def ping(self):
        return {"ok": True, "driver": "fake", "bucket": "b", "public_base_url": "https://cdn", "key_prefix": ""}


class FakeEncoder:
    def __init__(self, vector):
        self.vector = vector

    def fingerprint(self):
        return fingerprint(self.vector)

    def embed_bytes(self, data):
        return list(self.vector)


def collection(client, name, dim=DIM, distance="cosine"):
    store = QdrantStore(MEMORY, collection=name, vector_dim=dim, distance=distance, client=client)
    store.ensure_collection(payload_indexes=PAYLOAD_INDEXES)
    return QdrantVectorStore(store)


class Scenario:
    """一个当前版本、一个 ready 版本、一个 building 版本，各种不一致都有一处。"""

    def __init__(self):
        self.client = MEMORY.create_client()
        self.store = store = FakeStore()
        self.storage = storage = FakeStorage()
        store.models = [
            {"id": 1, "name": "dino", "version": "v1", "status": "current", "collection": "c_cur", "dim": DIM,
             "distance": "cosine", "fingerprint": fingerprint(VEC_A)},
            {"id": 2, "name": "dino", "version": "v2", "status": "ready", "collection": "c_ready", "dim": DIM,
             "distance": "cosine", "fingerprint": fingerprint(VEC_B)},
            {"id": 3, "name": "dino", "version": "v3", "status": "building", "collection": "c_build", "dim": DIM,
             "distance": "cosine", "fingerprint": None},
            {"id": 4, "name": "dino", "version": "v0", "status": "retired", "collection": "c_retired", "dim": DIM,
             "distance": "cosine", "fingerprint": None},
        ]
        self.ok, self.lost, self.unrecorded, self.behind, self.stale, self.missing = (store.add_image(n) for n in range(1, 7))
        store.images[self.behind]["payload_version"] = 2
        store.images[self.stale]["payload_version"] = 2
        self.cur = collection(self.client, "c_cur")
        self.ready = collection(self.client, "c_ready")
        self.build = collection(self.client, "c_build")
        collection(self.client, "c_retired")  # 下线时没删掉
        collection(self.client, "other_app")
        for iid in (self.ok, self.lost, self.behind, self.stale):
            store.ledger[(1, iid)] = 1 if iid == self.behind else store.images[iid]["payload_version"]
        for iid in (self.ok, self.unrecorded, self.behind, self.stale):
            payload = self.store.payload(iid)
            if iid in (self.behind, self.stale):
                payload = {**payload, "scenes": ["general", "oven"]}  # 旧的 payload
            self.cur.upsert(iid, VEC_A, payload)
        # 多出来的点：图片不在数据库里 / 在删除队列里 / 正在入库（还没提交）/ 不是图片 id
        self.orphan, self.deleted, self.committing = image_id(90), image_id(91), image_id(92)
        store.deletions[self.deleted] = {"original_url": f"https://cdn/original/00/{self.deleted}.jpg",
                                         "thumb_url": f"https://cdn/thumb/00/{self.deleted}.webp"}
        for iid in (self.orphan, self.deleted):
            self.cur.upsert(iid, VEC_A, {})
        store.add_image(92)
        store.hidden.add(self.committing)
        self.cur.upsert(self.committing, VEC_A, self.store.payload(self.committing))
        self.cur._store.client.upsert(  # 整数 id 的点：不是本服务写的
            collection_name="c_cur",
            points=[__import__("qdrant_client").http.models.PointStruct(id=42, vector={"global": VEC_A}, payload={})],
        )
        # ready 版本：只有第一张；building 版本：空的
        store.ledger[(2, self.ok)] = 1
        self.ready.upsert(self.ok, VEC_A, self.store.payload(self.ok))
        # 文件：第一张缺原图，第二张缩略图读不了，第三张原图内容不对；多一个孤儿文件，一个在删除队列里
        for iid, row in store.images.items():
            data = f"image-{uuid.UUID(iid).int}".encode()
            if iid != self.ok:
                storage.put(row["original_url"], b"other" if iid == self.unrecorded else data)
            storage.put(row["thumb_url"], b"thumb")
        storage.forbidden.add(store.images[self.lost]["thumb_url"].split("https://cdn/", 1)[-1])
        storage.objects[f"original/ab/{image_id(99)}.jpg"] = b"x"
        storage.objects[f"thumb/00/{self.deleted}.webp"] = b"t"
        self.encoders = {1: FakeEncoder(VEC_A), 2: FakeEncoder(VEC_A)}  # ready 版本的 collection 不是它的编码器写的

    def targets(self):
        vectors = {1: self.cur, 2: self.ready, 3: self.build}
        return [
            VersionTarget(row=row, vectors=vectors[row["id"]], encoder=self.encoders.get(row["id"]),
                          encoder_source="serving" if row["id"] == 1 else ("configured" if row["id"] == 2 else None),
                          serving=row["id"] == 1)
            for row in self.store.models if row["status"] != "retired"
        ]

    def check(self, options=CheckOptions(content=True), **kwargs):
        return ConsistencyCheck(
            metadata=self.store, versions=self.targets(), storage=self.storage, options=options, models=self.store.models,
            list_collections=self.cur.list_collections, **kwargs,
        )


def by_id(report):
    return {v["id"]: v for v in report["versions"]}


def counts(findings):
    return {name: f["count"] for name, f in findings.items() if f["count"]}


class ConsistencyCheckTests(unittest.TestCase):
    def setUp(self):
        self.s = Scenario()
        self.addCleanup(self.s.client.close)

    def test_every_kind_of_vector_difference_is_found_once(self):
        report = self.s.check().run()
        self.assertTrue(report["complete"])
        current = by_id(report)[1]
        self.assertEqual(current["qdrant"], {"exists": True, "dim": DIM, "distance": "cosine", "points": 8, "matches": True})
        self.assertEqual(counts(current["findings"]), {
            "missing": 1, "lost": 1, "unrecorded": 1, "ledger_behind": 1, "stale_payload": 1,
            "orphan_points": 2, "pending_points": 1,
        })
        f = current["findings"]
        self.assertEqual(f["lost"]["samples"], [self.s.lost])
        self.assertEqual(f["missing"]["samples"], [self.s.missing])
        self.assertEqual(f["unrecorded"]["samples"], [self.s.unrecorded])
        self.assertEqual(f["ledger_behind"]["samples"], [self.s.behind])  # 对账会补推：不算 stale
        self.assertEqual(f["stale_payload"]["samples"], [self.s.stale])  # 记录说是最新的，其实不是
        self.assertEqual(f["pending_points"]["samples"], [self.s.deleted])
        # 正在入库的那张：翻点时还没提交，最后再查一次时已经有了，不算多余
        self.assertEqual(sorted(f["orphan_points"]["samples"]), sorted([self.s.orphan, "42"]))
        self.assertEqual((current["scanned"], current["ledger"], current["with_vector"]), (6, 4, 4))
        self.assertIsNone(current["vs_current"])

    def test_versions_are_compared_with_the_current_one(self):
        versions = by_id(self.s.check().run())
        ready, building = versions[2], versions[3]
        self.assertEqual(ready["vs_current"]["only_current"]["count"], 3)  # 当前版本有点的 4 张里，它只有第一张
        self.assertEqual(ready["vs_current"]["only_this"]["count"], 0)
        self.assertEqual(ready["findings"]["missing"]["count"], 5)
        self.assertEqual(building["findings"]["missing"]["count"], 6)
        self.assertEqual(building["vs_current"]["only_current"]["count"], 4)

    def test_the_encoder_spot_check_tells_which_collection_another_encoder_wrote(self):
        versions = by_id(self.s.check().run())
        # ready 版本唯一的一张图原图不在了：重算不了，不下结论
        self.assertEqual(versions[2]["encoder"]["status"], "unchecked")
        self.s.storage.put(self.s.store.images[self.s.ok]["original_url"], b"image-1")
        versions = by_id(self.s.check().run())
        self.assertEqual(versions[1]["encoder"]["status"], "ok")
        self.assertEqual(versions[1]["encoder"]["source"], "serving")
        self.assertGreaterEqual(versions[1]["encoder"]["checked"], 1)
        self.assertEqual(versions[2]["encoder"]["status"], "ok")  # ready 版本的点是 VEC_A，它的"编码器"也给 VEC_A
        self.s.encoders[2] = FakeEncoder(VEC_B)
        versions = by_id(self.s.check().run())
        self.assertEqual(versions[2]["encoder"]["status"], "mismatch")
        self.assertLess(versions[2]["encoder"]["min_similarity"], 0.9)
        self.assertEqual(versions[3]["encoder"]["status"], "not_loaded")

    def test_files_missing_unreadable_changed_and_left_over(self):
        files = self.s.check().run()["files"]
        found = files["findings"]
        self.assertEqual(files["checked"], 6)
        self.assertEqual(found["missing_original"]["samples"], [self.s.ok])
        self.assertEqual(found["unreadable"]["samples"], [self.s.lost])
        self.assertIn("403", files["first_error"])
        self.assertEqual(found["content_mismatch"]["samples"], [self.s.unrecorded])
        self.assertEqual(found["orphan_files"]["samples"], [f"original/ab/{image_id(99)}.jpg"])
        self.assertEqual(found["pending_files"]["samples"], [f"thumb/00/{self.s.deleted}.webp"])
        # 7 张图（含核对期间提交的那张）的 6 个原图 + 7 个缩略图
        self.assertEqual(files["referenced"], 13)
        self.assertEqual(found["missing_thumb"]["count"], 0)

    def test_without_content_the_originals_are_not_downloaded(self):
        self.s.storage.read_by_url_or_key = Mock(side_effect=AssertionError("downloaded"))
        report = self.s.check(CheckOptions(content=False, encoders=False)).run()
        self.assertEqual(report["files"]["findings"]["content_mismatch"]["count"], 0)

    def test_stray_and_left_over_collections(self):
        report = self.s.check().run()
        stray = {item["name"]: item for item in report["stray_collections"]}
        self.assertEqual(set(stray), {"c_retired", "other_app"})
        self.assertEqual((stray["c_retired"]["model_id"], stray["c_retired"]["status"]), (4, "retired"))
        self.assertIsNone(stray["other_app"]["model_id"])

    def test_a_missing_or_misshapen_collection_is_reported_not_scanned(self):
        self.s.client.delete_collection("c_ready")
        self.s.client.delete_collection("c_build")
        collection(self.s.client, "c_build", dim=8)
        versions = by_id(self.s.check().run())
        self.assertFalse(versions[2]["qdrant"]["exists"])
        self.assertEqual(versions[2]["scanned"], 0)
        self.assertEqual(versions[2]["encoder"]["status"], "unchecked")
        self.assertEqual(versions[3]["qdrant"]["matches"], False)
        self.assertEqual(versions[3]["qdrant"]["dim"], 8)

    def test_a_stop_ends_after_the_batch_and_reports_what_was_checked(self):
        seen = []
        with patch.object(cc, "IMAGE_BATCH", 2):
            report = self.s.check(
                progress=lambda phase, done, total, label: seen.append((phase, done)),
                should_stop=lambda: any(phase == "images" and done > 0 for phase, done in seen),
            ).run()
        self.assertFalse(report["complete"])
        self.assertEqual(by_id(report)[1]["scanned"], 2)  # 第一批之后就停了
        self.assertEqual(by_id(report)[1]["encoder"]["status"], "off")
        self.assertNotIn("points", [phase for phase, _ in seen])
        self.assertIn("check_incomplete", [i["code"] for i in build_issues(report)])

    def test_progress_walks_through_the_phases(self):
        phases = []
        self.s.check(progress=lambda phase, done, total, label: phases.append(phase)).run()
        self.assertEqual(list(dict.fromkeys(phases)), ["collections", "images", "points", "files", "recheck", "encoders"])

    def test_a_mock_storage_without_mirror_is_skipped(self):
        report = self.s.check(storage_unavailable="mock storage keeps the bytes in memory only").run()
        self.assertEqual(report["files"]["skipped"], "mock storage keeps the bytes in memory only")
        self.assertEqual(report["files"]["checked"], 0)
        self.assertIn("files_unchecked", [i["code"] for i in build_issues(report)])

    def test_errors_are_redacted(self):
        self.s.storage.forbidden.add(self.s.store.images[self.s.ok]["thumb_url"].split("https://cdn/", 1)[-1])
        report = self.s.check(redact=lambda text: text.replace("bucket", "***")).run()
        self.assertNotIn("bucket", report["files"]["first_error"])


class IssuesTests(unittest.TestCase):
    def test_the_report_becomes_a_list_of_things_to_do_worst_first(self):
        s = Scenario()
        self.addCleanup(s.client.close)
        report = s.check().run()
        report["connections"] = {
            "postgres": {"ok": True}, "storage": {"ok": True},
            "qdrant": {"ok": True, "restart_required": True, "configured_url": "http://new:6333"},
            "serving": {"model_id": 1, "label": "dino:v1"},
        }
        issues = build_issues(report, now=datetime(2026, 11, 27, tzinfo=timezone.utc))
        codes = [(i["code"], i["version_id"]) for i in issues]
        self.assertEqual(issues[0]["level"], "error")
        for expected in [("qdrant_restart_required", None), ("lost_vectors", 1), ("stale_payloads", 1),
                         ("orphan_points", 1), ("missing_original", None), ("files_unreadable", None),
                         ("content_mismatch", None), ("versions_differ", 2), ("retired_collection_left", None),
                         ("stray_collections", None), ("deletion_queue", None), ("ledger_behind", 1)]:
            self.assertIn(expected, codes)
        levels = {(i["code"], i["version_id"]): i["level"] for i in issues}
        self.assertEqual(levels[("missing_vectors", 1)], "warn")
        self.assertEqual(levels[("missing_vectors", 3)], "info")  # building：本来就还在生成
        self.assertEqual(levels[("versions_differ", 3)], "info")
        self.assertEqual(levels[("deletion_queue", None)], "warn")  # 最早的一条已经很久了
        fresh = build_issues(report, now=datetime(2026, 11, 26, 8, 10, tzinfo=timezone.utc))
        self.assertEqual({(i["code"], i["version_id"]): i["level"] for i in fresh}[("deletion_queue", None)], "info")
        self.assertEqual(overall_status(issues), "error")
        self.assertEqual(overall_status([{"level": "info"}]), "ok")

    def test_search_on_another_version_or_none_is_flagged(self):
        report = {"connections": {"serving": {"model_id": 2, "label": "dino:v2"}}, "database": {"current_models": 1},
                  "versions": [{"id": 1, "label": "dino:v1", "status": "current", "dim": 4, "distance": "cosine",
                                "qdrant": {"exists": True, "matches": True}, "findings": {}, "encoder": {}}]}
        self.assertIn("serving_not_current", [i["code"] for i in build_issues(report)])
        report["connections"]["serving"] = {"model_id": None, "detail": "EMBEDDING_MODEL_MISMATCH"}
        report["database"]["current_models"] = 0
        codes = [i["code"] for i in build_issues(report)]
        self.assertIn("retrieval_not_ready", codes)
        self.assertIn("no_current_model", codes)


class QdrantReadTests(unittest.TestCase):
    def test_scroll_describe_and_list_are_read_only(self):
        client = MEMORY.create_client()
        self.addCleanup(client.close)
        vectors = collection(client, "c")
        ids = sorted(image_id(n) for n in range(1, 8))
        for iid in ids:
            vectors.upsert(iid, VEC_A, {})
        seen, offset, pages = [], None, 0
        while True:
            page, offset = vectors.scroll_ids(offset=offset, limit=3)
            seen += page
            pages += 1
            if offset is None:
                break
        self.assertEqual(sorted(seen), ids)
        self.assertEqual(pages, 3)
        self.assertEqual(vectors.describe(), {"size": DIM, "distance": "cosine", "points_count": 7})
        missing = QdrantVectorStore(QdrantStore(MEMORY, collection="nope", vector_dim=DIM, client=client))
        self.assertIsNone(missing.describe())
        self.assertNotIn("nope", vectors.list_collections())  # describe 不会创建它
        self.assertEqual(vectors.list_collections(), ["c"])


class StorageInventoryTests(unittest.TestCase):
    def test_mock_storage_with_a_mirror_can_be_checked(self):
        with tempfile.TemporaryDirectory() as mirror:
            storage = MockImageStorage(ImageStorageSettings(public_base_url="https://cdn.example.local", mock_mirror_dir=mirror))
            iid = image_id(5)
            url = storage.put_original(iid, b"abc", ext="jpg")
            thumb = storage.put_thumb(iid, b"t")
            self.assertIsNone(storage.inventory_unavailable())
            self.assertEqual(storage.head(url), 3)
            self.assertIsNone(storage.head(url.replace(iid, image_id(6))))
            # 重启之后：内存里没有了，镜像目录里还有
            again = MockImageStorage(ImageStorageSettings(public_base_url="https://cdn.example.local", mock_mirror_dir=mirror))
            self.assertEqual(again.head(thumb), 1)
            (Path(mirror) / "original" / "zz").mkdir()
            (Path(mirror) / "original" / "zz" / "stray.jpg").write_bytes(b"12")
            self.assertEqual(list(again.list_keys("original")), [("original/00/" + iid + ".jpg", 3), ("original/zz/stray.jpg", 2)])
            self.assertEqual(list(again.list_keys("thumb")), [("thumb/00/" + iid + ".webp", 1)])

    def test_mock_storage_without_a_mirror_says_why_it_cannot_be_checked(self):
        storage = MockImageStorage(ImageStorageSettings(public_base_url="https://cdn.example.local"))
        self.assertIn("mock_mirror_dir", storage.inventory_unavailable())
        with self.assertRaises(err.AppError) as ctx:
            storage.head("https://cdn.example.local/original/00/x.jpg")
        self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")

    def test_s3_head_tells_missing_from_unknown_and_lists_with_the_key_prefix(self):
        from botocore.exceptions import ClientError

        storage = S3ImageStorage.__new__(S3ImageStorage)
        storage.settings = ImageStorageSettings(use_boto3=True, bucket="b", public_base_url="https://b.oss", key_prefix="ImageEmbedding")
        storage.public_base_url, storage.bucket = "https://b.oss", "b"
        client = Mock()
        storage._client = client
        client.head_object.side_effect = [
            {"ContentLength": 12},
            ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject"),
            ClientError({"Error": {"Code": "403"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "HeadObject"),
        ]
        url = "https://b.oss/ImageEmbedding/original/aa/x.jpg"
        self.assertEqual(storage.head(url), 12)
        client.head_object.assert_called_with(Bucket="b", Key="ImageEmbedding/original/aa/x.jpg")
        self.assertIsNone(storage.head(url))
        with self.assertRaises(err.AppError) as ctx:
            storage.head(url)
        self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")
        client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "ImageEmbedding/thumb/aa/x.webp", "Size": 3}]}, {"Contents": []},
        ]
        self.assertEqual(list(storage.list_keys("thumb")), [("ImageEmbedding/thumb/aa/x.webp", 3)])
        client.get_paginator.assert_called_with("list_objects")
        client.get_paginator.return_value.paginate.assert_called_with(Bucket="b", Prefix="ImageEmbedding/thumb/")


class ConsistencyHttpTests(unittest.TestCase):
    """真实的路由、适配层和后台线程；Qdrant 是进程内的实例。"""

    def setUp(self):
        self.s = Scenario()
        self.addCleanup(self.s.client.close)
        self.s.client.close = Mock()  # 核对结束时会关闭它的客户端；这里要留着给下一次
        stack = SimpleNamespace(model=SimpleNamespace(id=1, space_id="dino:v1"), embedding=self.s.encoders[1],
                                infra=SimpleNamespace(storage=self.s.storage))
        self.runtime = ApplicationState(
            config={"QDRANT_CONFIG": {"url": ":memory:"}}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(store=self.s.store, file_storage=lambda: self.s.storage),
            embedder=FakeEncoder(VEC_B),  # EMBEDDING_CONFIG 是 ready 版本的模型
            retrieval=stack,
        )
        self.checker = ConsistencyChecker(self.runtime)
        self.runtime.consistency = self.checker
        self.addCleanup(self.checker.close)
        p = patch.object(QdrantConnection, "create_client", lambda _self: self.s.client)
        p.start()
        self.addCleanup(p.stop)
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(consistency_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(consistency_routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)

    def wait(self):
        for _ in range(200):
            check = self.client.get("/api/consistency").json()["check"]
            if check["state"] not in ("running", "stopping"):
                return check
            time.sleep(0.02)
        self.fail("the check did not finish")

    def test_nothing_checked_yet(self):
        response = self.client.get("/api/consistency")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "check": None})

    def test_a_check_runs_in_the_background_and_reports_the_connections(self):
        response = self.client.post("/api/consistency/check", json={"content": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["check"]["state"], ("running", "done"))
        check = self.wait()
        self.assertEqual(check["state"], "done", check.get("error"))
        report = check["report"]
        self.assertEqual(report["status"], "error")
        connections = report["connections"]
        self.assertEqual(connections["qdrant"]["url"], ":memory:")
        self.assertFalse(connections["qdrant"]["restart_required"])
        self.assertEqual(connections["postgres"]["host"], "pg")
        self.assertEqual(connections["serving"], {"model_id": 1, "label": "dino:v1", "detail": None})
        versions = by_id(report)
        self.assertEqual(versions[1]["encoder"]["source"], "serving")
        self.assertEqual(versions[2]["encoder"]["source"], "configured")
        self.assertIsNone(versions[3]["encoder"]["source"])
        self.assertTrue(versions[1]["serving"])
        self.assertIn("lost_vectors", [i["code"] for i in report["issues"]])

    def test_a_changed_qdrant_address_is_flagged_for_a_restart(self):
        self.runtime.config = {"QDRANT_CONFIG": {"url": "http://other:6333"}}  # Nacos 热更新
        self.client.post("/api/consistency/check", json={})
        report = self.wait()["report"]
        self.assertEqual(report["connections"]["qdrant"]["configured_url"], "http://other:6333")
        self.assertEqual(report["issues"][0]["code"], "qdrant_restart_required")

    def test_one_check_at_a_time_and_a_stop(self):
        gate = threading.Event()
        original = self.s.store.scan_images

        def slow(**kwargs):
            gate.wait(5)
            return original(**kwargs)

        self.s.store.scan_images = slow
        self.assertEqual(self.client.post("/api/consistency/check", json={}).status_code, 200)
        busy = self.client.post("/api/consistency/check", json={})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json()["error"], "CONSISTENCY_CHECK_RUNNING")
        stopped = self.client.post("/api/consistency/check/stop").json()["check"]
        self.assertEqual(stopped["state"], "stopping")
        gate.set()
        check = self.wait()
        self.assertEqual(check["state"], "stopped")
        self.assertFalse(check["report"]["complete"])

    def test_bad_options_are_refused(self):
        for body in ({"content": True, "files": False}, {"vectors": False, "files": False, "encoders": False}, {"x": 1}):
            with self.subTest(body=body):
                response = self.client.post("/api/consistency/check", json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertIsNone(self.client.get("/api/consistency").json()["check"])  # 没有开始

    def test_without_postgres_it_is_not_ready(self):
        self.runtime.catalog = None
        response = self.client.post("/api/consistency/check", json={})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "NOT_READY")
        self.runtime.consistency = None
        self.assertEqual(self.client.get("/api/consistency").status_code, 503)

    def test_a_check_that_cannot_reach_qdrant_still_checks_the_files(self):
        self.runtime.config = {"QDRANT_CONFIG": {}}
        self.checker = ConsistencyChecker(self.runtime)
        self.runtime.consistency = self.checker
        self.addCleanup(self.checker.close)
        self.client.post("/api/consistency/check", json={})
        report = self.wait()["report"]
        self.assertFalse(report["connections"]["qdrant"]["ok"])
        self.assertIn("QDRANT_CONFIG", report["connections"]["qdrant"]["detail"])
        self.assertTrue(all(v["error"] for v in report["versions"]))
        self.assertEqual(report["files"]["checked"], 6)
        codes = [i["code"] for i in report["issues"]]
        self.assertIn("qdrant_unreachable", codes)
        self.assertIn("version_unreadable", codes)


if __name__ == "__main__":
    unittest.main()
