"""迁移 + 仓储的真实 PostgreSQL 检查。

除非 TEST_DATABASE_URL 指向一个**空的**、可以随便丢弃的数据库
（例如 postgresql+psycopg://user:pw@localhost:5432/image_embedding_test），否则跳过。
每个测试都通过 Alembic 删除并重建表结构。
"""

import hashlib
import os
import unittest
import uuid
from pathlib import Path

TEST_URL = os.getenv("TEST_DATABASE_URL", "").strip()
BACKEND = Path(__file__).resolve().parents[1]


def _alembic(cmd: str, rev: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = TEST_URL  # alembic/env.py 用它代替 POSTGRES_CONFIG
    getattr(command, cmd)(cfg, rev)


# 测试数据：编写这些测试时依据的六个场景（迁移 0001 以前会插入它们；基础数据现在来自
# scripts/seed_data.py，那里只有前三个）。
FIXTURE_SCENES_SQL = """
INSERT INTO scenes (code, name, description, include_all, outputs_heating, is_active)
VALUES
  ('general', '通用（日常）', '含全部图片', true, false, true),
  ('oven', '一体机', '按类别规则 + scene_labels；结果带加热组', false, true, true),
  ('range_hood', '抽油烟机', '待配置规则', false, false, true),
  ('us_diet', '美国饮食', '菜系含 american', false, false, true),
  ('cn_diet', '中国饮食', '菜系含 chinese', false, false, true),
  ('jp_diet', '日本饮食', '菜系含 japanese', false, false, true)
"""


def _sha(n: int) -> bytes:
    return hashlib.sha256(f"image-{n}".encode()).digest()


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL not set")
class PgLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine

        cls.engine = create_engine(TEST_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _alembic("downgrade", "base")
        _alembic("upgrade", "head")
        self.sql(FIXTURE_SCENES_SQL)

    def sql(self, statement, params=None):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            return conn.execute(text(statement), params or {})

    def store(self):
        from app.repositories.metadata import PgMetadataStore

        return PgMetadataStore(url=TEST_URL)

    # ---- 表结构 ---------------------------------------------------------

    def test_migrated_schema_matches_the_orm_models(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        import app.models  # noqa: F401
        from app.db.base import Base

        with self.engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
        self.assertEqual(diff, [])

    def test_downgrade_to_0001_and_back(self):
        _alembic("downgrade", "20261104_0001")
        _alembic("upgrade", "head")
        self.test_migrated_schema_matches_the_orm_models()

    def test_0002_drops_include_all_rows_and_flags_manually_excluded_images(self):
        _alembic("downgrade", "20261104_0001")
        ids = [uuid.UUID(bytes=_sha(i)[:16]) for i in (1, 2)]
        for i, iid in enumerate(ids, start=1):
            self.sql(
                "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, "
                "source_type, license, label_status) VALUES "
                "(:id, :sha, 'https://x/o', 'https://x/t', 4, 3, 'user_upload', 'unknown', 'unlabeled')",
                {"id": iid, "sha": _sha(i)},
            )
        general = self.sql("SELECT id FROM scenes WHERE code = 'general'").scalar_one()
        self.sql(
            "INSERT INTO image_scenes (image_id, scene_id, origin, is_member) VALUES "
            "(:a, :s, 'rule', true), (:b, :s, 'manual', false)",
            {"a": ids[0], "b": ids[1], "s": general},
        )
        _alembic("upgrade", "head")
        self.assertEqual(self.sql("SELECT count(*) FROM image_scenes").scalar_one(), 0)
        versions = dict(self.sql("SELECT id, payload_version FROM images").fetchall())
        self.assertEqual(versions[ids[0]], 1)
        self.assertEqual(versions[ids[1]], 2)

    def test_check_constraints_reject_inconsistent_rows(self):
        from sqlalchemy.exc import IntegrityError

        with self.assertRaisesRegex(IntegrityError, "ck_images_label_matches_category"):
            self.sql(
                "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, "
                "source_type, license, label_status) VALUES "
                "(:id, :sha, 'https://x/o', 'https://x/t', 4, 3, 'user_upload', 'unknown', 'auto')",
                {"id": uuid.UUID(bytes=_sha(9)[:16]), "sha": _sha(9)},
            )
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        us = self.sql("SELECT id FROM scenes WHERE code = 'us_diet'").scalar_one()
        cat = self.sql("INSERT INTO categories (code) VALUES ('dish') RETURNING id").scalar_one()
        label = self.sql(
            "INSERT INTO scene_labels (scene_id, code, name) VALUES (:s, 'x', 'x') RETURNING id",
            {"s": us},
        ).scalar_one()
        with self.assertRaisesRegex(IntegrityError, "fk_scene_rules_scene_id_label_id_scene_labels"):
            self.sql(
                "INSERT INTO scene_rules (scene_id, category_id, label_id) VALUES (:s, :c, :l)",
                {"s": oven, "c": cat, "l": label},
            )

    # ---- 仓储 -----------------------------------------------------------

    def seed_catalog(self):
        hg = self.sql(
            "INSERT INTO heating_groups (code, name) VALUES ('grill', '烤') RETURNING id"
        ).scalar_one()
        dish = self.sql(
            "INSERT INTO categories (code, heating_group_id, cuisines) "
            "VALUES ('dish', :hg, '{american}') RETURNING id",
            {"hg": hg},
        ).scalar_one()
        steak = self.sql(
            "INSERT INTO categories (code, parent_id) VALUES ('dish.steak', :p) RETURNING id",
            {"p": dish},
        ).scalar_one()
        self.sql(
            "INSERT INTO category_names (category_id, lang, name, kind) VALUES (:c, 'zh', '牛排', 'primary')",
            {"c": steak},
        )
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        label = self.sql(
            "INSERT INTO scene_labels (scene_id, code, name) VALUES (:s, 'steak', '牛排') RETURNING id",
            {"s": oven},
        ).scalar_one()
        self.sql(
            "INSERT INTO scene_rules (scene_id, category_id, label_id) VALUES (:s, :c, :l)",
            {"s": oven, "c": dish, "l": label},
        )
        return {"dish": dish, "steak": steak, "label": label}

    def insert(self, store, n, **extra):
        from app.core.rules.image_ids import uuid_from_sha256

        sha = _sha(n)
        row = {
            "id": uuid_from_sha256(sha),
            "sha256": sha,
            "original_url": f"https://cdn/o/{n}.jpg",
            "thumb_url": f"https://cdn/t/{n}.webp",
            "width": 640,
            "height": 480,
        }
        row.update(extra)
        iid = store.insert_image(row)
        store.recompute_image_scenes(iid, bump_version=False)
        return str(iid)

    def test_index_list_and_scene_membership_flow(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        steak_img = self.insert(store, 1, category_id=cat["steak"], label_status="auto", food_state="cooked")
        plain_img = self.insert(store, 2)

        # general 不需要 image_scenes 行就包含所有图片；steak 匹配 oven 的规则
        rows = self.sql("SELECT scene_id FROM image_scenes WHERE image_id = :i", {"i": steak_img}).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(store.get_effective_scene_codes(steak_img), ["general", "oven"])
        self.assertEqual(store.get_effective_scene_codes(plain_img), ["general"])

        items, total = store.list_images(scene="general")
        self.assertEqual(total, 2)
        by_id = {i["id"]: i for i in items}
        self.assertEqual(by_id[steak_img]["category_name"], "牛排")
        self.assertEqual(by_id[steak_img]["heating_group"]["code"], "grill")
        self.assertEqual(store.list_images(scene="oven")[1], 1)
        self.assertEqual(store.list_images(scene="general", q="牛排")[1], 1)
        self.assertEqual(store.list_images(scene="nope"), ([], 0))

        hydrated = store.get_active_by_ids([steak_img, plain_img], scene="oven")
        self.assertEqual(list(hydrated), [steak_img])
        self.assertEqual(len(store.get_active_by_ids([steak_img, plain_img], scene="general")), 2)

        oven = store.get_scene_by_code("oven")["id"]
        labels = store.map_categories_to_scene_labels(oven, [cat["steak"], cat["dish"]])
        self.assertEqual({cid: label["code"] for cid, label in labels.items()}, {cat["steak"]: "steak", cat["dish"]: "steak"})

        with self.assertRaises(AppError):
            store.set_manual_scene_membership(steak_img, "general", is_member=False)
        out = store.set_manual_scene_membership(steak_img, "oven", is_member=False)
        self.assertEqual(out["scene_codes"], ["general"])
        self.assertEqual(out["payload_version"], 2)
        self.assertEqual(store.list_images(scene="oven")[1], 0)

    def test_same_bytes_keep_the_first_row(self):
        store = self.store()
        first = self.insert(store, 1)
        again = self.insert(store, 1, source_type="crawled", license="commercial")
        self.assertEqual(first, again)
        row = store.get_image_by_uuid(first)
        self.assertEqual((row["source_type"], row["license"]), ("user_upload", "unknown"))
        self.assertEqual(self.sql("SELECT count(*) FROM images").scalar_one(), 1)

    def test_image_patch_validates_and_resolves_the_category_label(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        iid = self.insert(store, 1)
        for fields in (
            {"food_state": "boiled"},
            {"food_count": True},
            {"label_status": None},
            {"original_url": "https://other.example/replacement.png"},
            {"width": 100},
            {"source_type": "crawled"},
            {"license": "commercial"},
            {"payload_version": 999},
            {},
        ):
            with self.subTest(fields=fields), self.assertRaises(AppError) as ctx:
                store.patch_image(iid, fields)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(store.get_by_id(iid)["payload_version"], 1)
        # 唯一的更新入口使用和图库编辑相同的类别 / 标注规则。
        store.patch_image(iid, {"category_id": cat["steak"]})
        row = store.get_by_id(iid)
        self.assertEqual((row["label_status"], row["payload_version"]), ("verified", 2))
        self.assertEqual(row["scenes"], ["general", "oven"])
        # 再次发送已存的 payload 值，对同步来说什么都不做。
        out = store.patch_image(iid, {"category_id": cat["steak"], "label_status": "verified"})
        self.assertEqual(out["payload_version"], 2)

    def test_deletion_queue_and_current_model(self):
        store = self.store()
        iid = self.insert(store, 1)
        store.enqueue_image_deletion(iid)
        self.assertIsNone(store.get_image_by_uuid(iid))
        self.assertEqual([r["image_id"] for r in store.list_pending_deletions()], [iid])
        store.complete_deletion(iid)
        self.assertEqual(store.list_pending_deletions(), [])

        self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, "
            "collection, status) VALUES ('m', '1', 1024, 'cosine', 336, 's3://w', 'c1', 'current')"
        )
        model = store.get_current_embedding_model()
        self.assertEqual((model["vector_dim"], model["distance"]), (1024, "cosine"))
        status = self.sql("INSERT INTO embedding_models (name, version, dim, distance, input_size, "
                          "weights_uri, collection) VALUES ('m', '2', 1024, 'cosine', 336, 's3://w', 'c2') "
                          "RETURNING status").scalar_one()
        self.assertEqual(status, "building")

    def test_a_point_written_again_holds_the_image_until_its_ledger_row_is_in(self):
        """lock_image_for_vector（reembed）：删除会等待写入，所以它的清理在向量点之后进行；
        先到的删除会让这里没东西可写。
        """
        import threading

        store = self.store()
        iid = self.insert(store, 1)
        model_id = self.current_model("c_reembed")
        ledger = lambda: [tuple(r) for r in self.sql("SELECT image_id::text, synced_version FROM image_embeddings").fetchall()]

        with self.assertRaises(RuntimeError), store.lock_image_for_vector(iid, model_id=model_id):
            raise RuntimeError("the upsert failed")
        self.assertEqual(ledger(), [])  # 已回滚
        with store.lock_image_for_vector(iid, model_id=model_id) as current:
            self.assertEqual((current["payload_version"], current["scenes"]), (1, ["general"]))
        self.assertEqual(ledger(), [(str(iid), 1)])

        done, failure = threading.Event(), []

        def delete():
            try:
                store.enqueue_image_deletion(iid)
            except Exception as exc:  # 在下面暴露出来
                failure.append(exc)
            done.set()

        worker = threading.Thread(target=delete, daemon=True)
        with store.lock_image_for_vector(iid, model_id=model_id) as current:
            self.assertIsNotNone(current)
            worker.start()
            self.assertFalse(done.wait(0.6), "the deletion did not wait for the point being written")
        self.assertTrue(done.wait(10), "the deletion never went on after the write")
        worker.join(10)
        self.assertEqual(failure, [])
        self.assertIsNone(store.get_image_by_uuid(iid))
        # 在写入之后排队：它的清理会删除刚写入的向量点
        self.assertEqual([str(r["image_id"]) for r in store.list_pending_deletions()], [str(iid)])
        self.assertEqual(ledger(), [])  # 随图片一起删除了

        with store.lock_image_for_vector(iid, model_id=model_id) as current:
            self.assertIsNone(current)  # 已删除：不能写入向量点
        self.assertEqual(ledger(), [])

    # ---- 图库管理读取和编辑的单张图片（patch_image / get_image_detail）----

    def current_model(self, collection):
        return self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', :c, 'current') RETURNING id",
            {"c": collection},
        ).scalar_one()

    def test_image_detail_and_patch(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        iid = self.insert(store, 1, tags=["带骨"], food_state="raw")
        detail = store.get_image_detail(iid)
        self.assertEqual(
            (detail["category_id"], detail["category_code"], detail["label_status"], detail["scenes"]),
            (None, None, "unlabeled", ["general"]),
        )
        self.assertEqual((detail["food_state"], detail["tags"], detail["embeddings"], detail["heating_group"]),
                         ("raw", ["带骨"], [], None))
        self.assertEqual(len(detail["sha256"]), 64)  # 十六进制
        self.assertIsNone(store.get_image_detail(uuid.uuid4()))
        model_id = self.current_model("c_detail")
        store.upsert_image_embedding(model_id=model_id, image_id=iid, synced_version=1)
        self.assertEqual(
            [(e["model_id"], e["collection"], e["model_status"], e["synced_version"])
             for e in store.get_image_detail(iid)["embeddings"]],
            [(model_id, "c_detail", "current", 1)],
        )

        # 手工选的类别：verified，规则归属跟着变，递增一次
        out = store.patch_image(iid, {"category_id": cat["steak"]})
        self.assertEqual(
            (out["label_status"], out["category_name"], out["category_code"], out["scenes"], out["payload_version"]),
            ("verified", "牛排", "dish.steak", ["general", "oven"], 2),
        )
        self.assertEqual(out["heating_group"]["code"], "grill")  # 最近祖先的
        # payload 以外的字段，以及和已存值相同的 food_state：不递增
        out = store.patch_image(iid, {"tags": ["去骨"], "description": "煎", "total_mass_g": 300, "food_state": "raw"})
        self.assertEqual((out["tags"], out["description"], out["total_mass_g"], out["payload_version"]),
                         (["去骨"], "煎", 300, 2))
        out = store.patch_image(iid, {"food_state": "cooked", "label_status": "auto"})
        self.assertEqual((out["food_state"], out["label_status"], out["payload_version"]), ("cooked", "auto", 3))
        # 类别被清空：未标注，并退出规则场景
        out = store.patch_image(iid, {"category_id": None})
        self.assertEqual((out["category_id"], out["label_status"], out["scenes"], out["payload_version"]),
                         (None, "unlabeled", ["general"], 4))
        self.assertEqual(
            self.sql("SELECT count(*) FROM image_scenes WHERE image_id = :i", {"i": uuid.UUID(iid)}).scalar_one(), 0
        )

        def refused(fields, code="VALIDATION_ERROR", image=iid):
            with self.assertRaises(AppError) as ctx:
                store.patch_image(image, fields)
            self.assertEqual(ctx.exception.code, code)

        refused({"label_status": "auto"})  # 没有类别能和它对应
        self.sql("UPDATE categories SET is_active = false WHERE id = :c", {"c": cat["dish"]})
        refused({"category_id": cat["dish"]})  # 已停用：不再用于标注
        refused({"category_id": 999999})
        refused({"description": "x"}, "IMAGE_NOT_FOUND", image=str(uuid.uuid4()))
        self.assertEqual(store.get_image_detail(iid)["payload_version"], 4)  # 没有写了一半的东西

    def test_list_filters_on_a_category_subtree_and_the_label_status(self):
        cat = self.seed_catalog()  # dish > dish.steak
        store = self.store()
        dish = self.insert(store, 1, category_id=cat["dish"], label_status="auto")
        steak = self.insert(store, 2, category_id=cat["steak"], label_status="verified")
        plain = self.insert(store, 3)

        def ids(**kw):
            return sorted(item["id"] for item in store.list_images(scene="general", **kw)[0])

        self.assertEqual(ids(category_id=cat["dish"]), sorted([dish, steak]))  # 这个类别及其子树
        self.assertEqual(ids(category_id=cat["steak"]), [steak])
        self.assertEqual(ids(category_id=999999), [])
        self.assertEqual(ids(label_status="unlabeled"), [plain])
        self.assertEqual(ids(category_id=cat["dish"], label_status="verified"), [steak])
        self.assertEqual(store.list_images(scene="general", category_id=cat["dish"])[1], 2)  # 总数也遵循它

    def test_list_out_of_sync_follows_the_current_model_s_ledger(self):
        store = self.store()
        synced, behind, never = (self.insert(store, n) for n in (1, 2, 3))

        def ids(**kw):
            return sorted(item["id"] for item in store.list_images(scene="general", **kw)[0])

        def model(version, status):
            return self.sql(
                "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
                "VALUES ('dino', :v, 4, 'cosine', 224, 'w', :c, :s) RETURNING id",
                {"v": version, "c": f"c_{version}", "s": status},
            ).scalar_one()

        self.assertEqual(ids(out_of_sync=True), sorted([synced, behind, never]))  # 没有当前模型：全部
        old, current = model("v1", "retired"), model("v2", "current")
        for image, mid, version in ((synced, current, 1), (behind, current, 1), (never, old, 5)):
            self.sql("INSERT INTO image_embeddings (model_id, image_id, synced_version) VALUES (:m, :i, :v)",
                     {"m": mid, "i": uuid.UUID(image), "v": version})
        self.sql("UPDATE images SET payload_version = 2 WHERE id = :i", {"i": uuid.UUID(behind)})
        # behind：台账 v1 < payload v2；never：只有旧模型写过它
        self.assertEqual(ids(out_of_sync=True), sorted([behind, never]))
        self.assertEqual(store.list_images(scene="general", out_of_sync=True)[1], 2)  # 总数也遵循它
        self.assertEqual(ids(), sorted([synced, behind, never]))  # 默认关闭

    def test_unsynced_payload_ids_are_ledger_rows_behind_in_id_order(self):
        store = self.store()
        images = sorted(self.insert(store, n) for n in range(1, 6))
        current = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('dino', 'v2', 4, 'cosine', 224, 'w', 'c_v2', 'current') RETURNING id"
        ).scalar_one()
        for image in images[:4]:  # images[4] 从来没有写进当前模型：不返回
            self.sql("INSERT INTO image_embeddings (model_id, image_id, synced_version) VALUES (:m, :i, 1)",
                     {"m": current, "i": uuid.UUID(image)})
        self.assertEqual(store.list_unsynced_payload_ids(), [])
        behind = [images[0], images[2], images[3]]
        self.sql("UPDATE images SET payload_version = 2 WHERE id = ANY(:ids)", {"ids": [uuid.UUID(i) for i in behind + [images[4]]]})

        self.assertEqual(store.list_unsynced_payload_ids(), behind)
        self.assertEqual(store.list_unsynced_payload_ids(limit=2), behind[:2])
        self.assertEqual(store.list_unsynced_payload_ids(after=images[0]), behind[1:])
        self.assertEqual(store.list_unsynced_payload_ids(until=images[2]), behind[:2])  # 含边界
        self.assertEqual(store.list_unsynced_payload_ids(after=images[0], until=images[2]), [images[2]])
        store.mark_payloads_pushed(model_id=current, versions={images[2]: 2})
        self.assertEqual(store.list_unsynced_payload_ids(), [images[0], images[3]])

    def test_model_versions_register_fill_switch_and_retire(self):
        from app.core.errors import AppError

        store = self.store()
        images = sorted(self.insert(store, n) for n in range(1, 5))
        old_fp = {"spec": {"model_path": "old.onnx"}, "probe": [1.0, 0.0, 0.0, 0.0]}
        current = self.current_model("c_old")  # 手工登记的，在有指纹之前（0009）
        self.assertIsNone(store.get_current_embedding_model()["fingerprint"])
        self.assertTrue(store.record_model_fingerprint(current, old_fp))
        self.assertFalse(store.record_model_fingerprint(current, {"spec": {}, "probe": [0.5]}))  # 从不覆盖
        self.assertEqual(store.get_current_embedding_model()["fingerprint"], old_fp)
        self.assertEqual(store.record_embeddings(model_id=current, versions={i: 1 for i in images}), images)

        fields = {"name": "dino", "version": "v2", "dim": 4, "distance": "cosine", "input_size": 224,
                  "preprocess": {"scale": [224, 224]}, "weights_uri": "new.onnx", "collection": "c_new",
                  "fingerprint": {"spec": {"model_path": "new.onnx"}, "probe": [0.0, 1.0, 0.0, 0.0]}}
        new = store.register_embedding_model(fields)
        self.assertEqual((new["status"], new["collection"], new["preprocess"]), ("building", "c_new", {"scale": [224, 224]}))
        self.assertEqual(new["fingerprint"], fields["fingerprint"])
        for clash in ({"collection": "c_other"}, {"version": "v3"}):  # 名称 + 版本被占用，collection 被占用
            with self.subTest(clash=clash), self.assertRaises(AppError) as ctx:
                store.register_embedding_model({**fields, **clash})
            self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_EXISTS")

        overview = store.list_embedding_models()
        self.assertEqual(overview["images"], 4)
        self.assertEqual({m["id"]: (m["status"], m["embedded"], m["behind"]) for m in overview["models"]},
                         {current: ("current", 4, 0), new["id"]: ("building", 0, 0)})
        self.assertEqual([r["id"] for r in store.list_embedding_model_rows(statuses=("building", "ready"))], [new["id"]])
        self.assertEqual(len(store.list_embedding_model_rows()), 2)

        # 待处理列表：该版本下没有向量的图片，按 id 顺序，在游标之后，去掉已经试过的
        self.assertEqual(store.count_embedding_backlog(new["id"]), 4)
        self.assertEqual([r["id"] for r in store.list_embedding_backlog(new["id"], limit=2)], images[:2])
        self.assertEqual([r["id"] for r in store.list_embedding_backlog(new["id"], after=images[1])], images[2:])
        self.assertEqual([r["id"] for r in store.list_embedding_backlog(new["id"], exclude=[images[0], images[2]])],
                         [images[1], images[3]])
        self.assertTrue(store.list_embedding_backlog(new["id"])[0]["original_url"].startswith("https://cdn/o/"))
        # 记录时跳过这期间被删除的图片（它的台账行随之删除）
        store.enqueue_image_deletion(images[3])
        self.assertEqual(store.record_embeddings(model_id=new["id"], versions={images[0]: 1, images[1]: 1, images[3]: 1}),
                         images[:2])
        self.assertEqual(store.count_embedding_backlog(new["id"]), 1)
        self.assertEqual([s["id"] for s in store.sample_embedded_images(new["id"], limit=5)], images[:2])
        # 向量写入之后 payload 又变了：在那个版本的台账里落后
        self.sql("UPDATE images SET payload_version = 3 WHERE id = :i", {"i": uuid.UUID(images[1])})
        self.assertEqual(store.list_unsynced_payload_ids(model_id=new["id"]), [images[1]])
        self.assertEqual({m["id"]: m["behind"] for m in store.list_embedding_models()["models"]}, {current: 1, new["id"]: 1})
        store.record_embeddings(model_id=new["id"], versions={images[1]: 3})  # 重新计算了 embedding：已是最新
        self.assertEqual(store.list_unsynced_payload_ids(model_id=new["id"]), [])

        # 切换：同一时间只有一个当前版本，之前的保持 ready 以便回滚
        result = store.activate_embedding_model(new["id"])
        self.assertEqual((result["previous_id"], result["model"]["status"]), (current, "current"))
        self.assertIsNotNone(result["model"]["activated_at"])
        self.assertEqual(store.get_embedding_model(current)["status"], "ready")
        self.assertEqual(store.get_current_embedding_model()["id"], new["id"])
        self.assertIsNone(store.activate_embedding_model(new["id"])["previous_id"])  # 已经是当前版本
        self.assertEqual(store.activate_embedding_model(current)["previous_id"], new["id"])  # 回滚
        store.activate_embedding_model(new["id"])

        # building -> ready 只能从预期的状态切换
        third = store.register_embedding_model({**fields, "version": "v3", "collection": "c3"})
        self.assertFalse(store.set_embedding_model_status(third["id"], "ready", expected=("ready",)))
        self.assertTrue(store.set_embedding_model_status(third["id"], "ready", expected=("building",)))
        self.assertEqual(store.get_embedding_model(third["id"])["status"], "ready")

        # 下线：永远不能是当前版本；它的台账行删除，这一行保留为 retired
        with self.assertRaises(AppError) as ctx:
            store.retire_embedding_model(new["id"])
        self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_STATE")
        out = store.retire_embedding_model(current)
        self.assertEqual((out["model"]["status"], out["ledger_rows"]), ("retired", 3))  # images[3] 已删除
        self.assertEqual(self.sql("SELECT count(*) FROM image_embeddings WHERE model_id = :m", {"m": current}).scalar_one(), 0)
        for call, code in ((lambda: store.activate_embedding_model(current), "EMBEDDING_MODEL_STATE"),
                           (lambda: store.activate_embedding_model(9999), "EMBEDDING_MODEL_NOT_FOUND"),
                           (lambda: store.retire_embedding_model(9999), "EMBEDDING_MODEL_NOT_FOUND")):
            with self.subTest(code=code), self.assertRaises(AppError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, code)
        self.assertIsNone(store.get_embedding_model(9999))
        self.assertEqual(self.sql("SELECT count(*) FROM embedding_models WHERE status = 'current'").scalar_one(), 1)

    def test_the_ledger_can_be_checked_against_the_collection_and_follow_moved_weights(self):
        store = self.store()
        images = sorted(self.insert(store, n) for n in range(1, 6))
        current = self.current_model("c_ledger")
        other = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '2', 4, 'cosine', 336, 's3://w', 'c_other', 'ready') RETURNING id"
        ).scalar_one()
        store.record_embeddings(model_id=current, versions={i: 1 for i in images})
        store.record_embeddings(model_id=other, versions={images[0]: 1})
        self.assertEqual(store.list_embedded_ids(current), images)
        self.assertEqual(store.list_embedded_ids(current, after=images[1], limit=2), images[2:4])
        self.assertEqual(store.forget_embeddings(current, [images[1], images[3], "00000000-0000-4000-8000-000000000999"]), 2)
        self.assertEqual(store.forget_embeddings(current, []), 0)
        self.assertEqual(store.list_embedded_ids(current), [images[0], images[2], images[4]])
        self.assertEqual(store.count_embedding_backlog(current), 2)  # 被忘掉的行：重新算作没有向量
        self.assertEqual(store.clear_embeddings(current), 3)
        self.assertEqual(store.list_embedded_ids(other), [images[0]])  # 另一个版本的台账不受影响
        moved = {"spec": {"model_path": "config/model/embedding/embeddingcls-abc/model.onnx"}, "probe": [1.0, 0, 0, 0],
                 "files": [{"name": "model.onnx", "size": 3, "sha256": "ab" * 32}], "artifact": "ab" * 32}
        self.assertTrue(store.refresh_model_fingerprint(current, moved))
        row = store.get_embedding_model(current)
        self.assertEqual((row["fingerprint"], row["weights_uri"]), (moved, moved["spec"]["model_path"]))
        self.assertFalse(store.refresh_model_fingerprint(9999, moved))

    def test_file_urls_come_from_the_image_or_its_queued_deletion(self):
        store = self.store()
        image = self.insert(store, 1)
        urls = {"original_url": "https://cdn/o/1.jpg", "thumb_url": "https://cdn/t/1.webp"}
        self.assertEqual(store.get_image_file_urls(image), {**urls, "deleted": False})
        store.enqueue_image_deletion(image)  # 已从 images 删除，文件在等待清理
        self.assertEqual(store.get_image_file_urls(image), {**urls, "deleted": True})
        store.complete_deletion(image)
        self.assertIsNone(store.get_image_file_urls(image))

    def test_consistency_reads_images_with_their_ledger_files_and_the_deletion_queue(self):
        from app.core.rules.image_ids import uuid_from_sha256

        store = self.store()
        by_n = {n: self.insert(store, n) for n in range(1, 4)}
        images = sorted(by_n.values())
        current = self.current_model("c_check")
        store.record_embeddings(model_id=current, versions={images[0]: 1})
        rows = store.scan_images(limit=2)
        self.assertEqual([row["id"] for row in rows], images[:2])
        self.assertEqual((rows[0]["ledger"], rows[1]["ledger"]), ({current: 1}, {}))
        first_n = next(n for n, iid in by_n.items() if iid == images[0])
        self.assertEqual(rows[0]["sha256"], _sha(first_n).hex())
        self.assertEqual(str(uuid_from_sha256(bytes.fromhex(rows[0]["sha256"]))), images[0])
        self.assertEqual((rows[0]["scenes"], rows[0]["label_status"], rows[0]["payload_version"]), (["general"], "unlabeled", 1))
        self.assertEqual(rows[0]["original_url"], f"https://cdn/o/{first_n}.jpg")
        self.assertEqual([row["id"] for row in store.scan_images(after=images[1])], images[2:])
        stranger = "00000000-0000-4000-8000-000000000999"
        self.assertEqual(store.existing_image_ids(images + [stranger]), set(images))
        self.assertEqual(store.existing_image_ids([]), set())
        store.enqueue_image_deletion(images[2])
        self.assertEqual(store.queued_deletion_ids(images + [stranger]), {images[2]})
        refs = store.file_refs(images + [stranger])
        self.assertEqual(set(refs), set(images))
        self.assertEqual([ref["deleted"] for ref in refs[images[2]]], [True])
        self.assertEqual([ref["deleted"] for ref in refs[images[0]]], [False])
        counts = store.consistency_counts()
        self.assertEqual((counts["images"], counts["pending_deletions"], counts["current_models"]), (2, 1, 1))
        self.assertIsNotNone(counts["oldest_deletion_at"])

    def test_a_maintenance_lock_lets_one_process_in_at_a_time(self):
        first, second = self.store(), self.store()  # 两个连接池：两个会话，相当于两个进程
        with first.try_maintenance_lock(42) as held:
            self.assertTrue(held)
            with second.try_maintenance_lock(42) as other:
                self.assertFalse(other)
            with second.try_maintenance_lock(43) as unrelated:
                self.assertTrue(unrelated)
        with second.try_maintenance_lock(42) as after_release:
            self.assertTrue(after_release)
        # 持有锁期间没有事务一直开着
        with first.try_maintenance_lock(42):
            open_tx = self.sql(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
            ).scalar_one()
            self.assertEqual(open_tx, 0)

    def test_a_deleted_image_stored_again_is_deleted_again(self):
        from app.core.errors import AppError

        store = self.store()
        with self.assertRaises(AppError) as ctx:
            store.enqueue_image_deletion(uuid.uuid4())
        self.assertEqual(ctx.exception.code, "IMAGE_NOT_FOUND")
        iid = self.insert(store, 1)
        first = store.enqueue_image_deletion(iid)
        self.assertEqual(store.enqueue_image_deletion(iid), first)  # 仍在排队：同一行
        self.assertEqual(self.insert(store, 1), iid)  # 在清理运行之前，同样的字节又入库了一次
        store.enqueue_image_deletion(iid)
        self.assertIsNone(store.get_image_by_uuid(iid))
        self.assertEqual([r["image_id"] for r in store.list_pending_deletions()], [iid])

    # ---- 任务中心：index_tasks 和删除队列计数 ----------

    def test_index_records_list_with_the_image_as_it_is_now_and_clear_by_status(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        kept = self.insert(store, 1, category_id=cat["steak"], label_status="verified")
        gone = self.insert(store, 2)
        store.record_index_tasks([  # 一个事务：created_at 相同，用 id 区分先后
            {"filename": "gone.jpg", "status": "succeeded", "image_id": gone, "new_image": True,
             "duration_ms": 640, "trace_id": "t-1"},
            {"filename": "junk.png", "status": "failed", "error_code": "INDEX_BAD_IMAGE",
             "error_detail": "cannot decode image", "duration_ms": 4, "trace_id": "t-2",
             "image_id": kept},  # 失败的记录从不保留图片 id
        ])
        store.record_index_tasks([{"filename": "x" * 300, "status": "succeeded", "image_id": kept, "new_image": False}])
        store.enqueue_image_deletion(gone)

        result = store.list_index_tasks()
        # x…：保留的图片又入库了一次（new_image 为 false）：一次重复，它的图片仍然存在
        self.assertEqual(
            (result["total"], result["counts"]),
            (3, {"succeeded": 2, "failed": 1, "image_deleted": 1, "repeated": 1}),
        )
        newest, failed, deleted = result["items"]
        self.assertEqual((len(newest["filename"]), newest["filename"][-1]), (255, "…"))  # 截断了
        self.assertEqual(
            (newest["image_exists"], newest["category_name"], newest["image_label_status"], newest["new_image"]),
            (True, "牛排", "verified", False),
        )
        self.assertEqual((newest["image_id"], newest["thumb_url"]), (kept, "https://cdn/t/1.webp"))
        self.assertEqual((failed["image_id"], failed["error_code"], failed["image_exists"]), (None, "INDEX_BAD_IMAGE", False))
        self.assertEqual((deleted["image_id"], deleted["image_exists"], deleted["thumb_url"]), (gone, False, None))
        self.assertIsInstance(deleted["created_at"], str)

        page = store.list_index_tasks(status="succeeded", page=2, page_size=1)
        self.assertEqual((page["total"], [r["filename"] for r in page["items"]]), (2, ["gone.jpg"]))
        self.assertEqual(page["counts"], {"succeeded": 2, "failed": 1, "image_deleted": 1, "repeated": 1})
        # 图片状态把成功的记录分开：仍然存在 / 之后被删除了
        for state, ids in (("present", [newest["id"]]), ("deleted", [deleted["id"]])):
            listed = store.list_index_tasks(image_state=state)
            self.assertEqual((listed["total"], [r["id"] for r in listed["items"]]), (1, ids))
        listed = store.list_index_tasks(status="failed", image_state="deleted")
        self.assertEqual((listed["total"], listed["items"]), (0, []))
        # new_image 也把它们分开：存了图片的上传 / 重复上传已存的图片
        for new_image, ids in ((False, [newest["id"]]), (True, [deleted["id"]])):
            listed = store.list_index_tasks(new_image=new_image)
            self.assertEqual((listed["total"], [r["id"] for r in listed["items"]]), (1, ids))
        listed = store.list_index_tasks(status="succeeded", image_state="present", new_image=True)
        self.assertEqual((listed["total"], listed["items"]), (0, []))  # 唯一一张仍存的图片的记录是一次重复
        listed = store.list_index_tasks(status="failed", new_image=True)
        self.assertEqual((listed["total"], listed["items"]), (0, []))

        with self.assertRaises(AppError) as ctx:  # 成功的记录指明它的图片
            store.record_index_tasks([{"filename": "a.png", "status": "succeeded"}])
        self.assertEqual(ctx.exception.code, "INDEX_POSTGRES_FAILED")
        self.assertEqual(store.list_index_tasks()["total"], 3)

        # 清空删除的正好是同一个过滤条件列出的内容，并且只删记录
        self.assertEqual(store.clear_index_tasks(image_state="deleted"), 1)
        self.assertEqual(store.list_index_tasks()["counts"], {"succeeded": 1, "failed": 1, "image_deleted": 0, "repeated": 1})
        self.assertEqual(store.clear_index_tasks(status="failed"), 1)
        self.assertEqual(store.list_index_tasks()["counts"], {"succeeded": 1, "failed": 0, "image_deleted": 0, "repeated": 1})
        with self.assertRaises(ValueError):
            store.clear_index_tasks()
        self.assertEqual((store.delete_index_task(newest["id"]), store.delete_index_task(newest["id"])), (1, 0))
        self.assertEqual(store.list_index_tasks()["total"], 0)
        self.assertIsNotNone(store.get_image_by_uuid(kept))
        self.assertEqual(store.count_pending_deletions(), 1)
        store.complete_deletion(gone)
        self.assertEqual(store.count_pending_deletions(), 0)

    def test_the_image_admin_reads_edits_and_deletes_through_the_vector_store(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1 import images as routes
        from app.bootstrap.state import ApplicationState
        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.core.exceptions import register_exception_handlers
        from app.core.middleware import TraceIdMiddleware
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.delete_image import DeletionWorker
        from app.services.retrieval.manage_images import ManageImagesUseCase
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
        from app.services.retrieval.vector_status import ImageVectorStatusUseCase

        cat = self.seed_catalog()
        store = self.store()
        model_id = self.current_model("c_images")
        iid = self.insert(store, 1)
        orphan = self.insert(store, 2)  # 在 PG 里，从来没有写进这个 collection
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_images", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        # payload 落后于 PostgreSQL 的向量点（label_status）
        vectors.upsert(iid, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "auto"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_images", dim=4, distance="cosine")
        storage = Mock()
        runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(images_uc=ManageImagesUseCase(store)),
            retrieval=SimpleNamespace(
                model=model,
                scene_sync_uc=SyncSceneMembershipsUseCase(store, vectors, model=model),
                vector_status_uc=ImageVectorStatusUseCase(vectors, model=model),
                deletion_worker=DeletionWorker(store, vectors, storage),
            ),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(routes, "get_application_state", return_value=runtime):
            def vector(image):
                response = client.get(f"/api/images/{image}")
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()["vector"]

            self.assertEqual(vector(iid)["status"], "stale")
            self.assertEqual(vector(orphan)["status"], "missing")
            body = client.post(f"/api/images/{iid}/vector-sync").json()
            self.assertEqual((body["vector_sync"], body["sync_required"]), ("pushed", False))
            state = vector(iid)
            self.assertEqual((state["status"], state["synced_version"], state["payload_version"]), ("synced", 1, 1))

            # 在图库里编辑的类别在同一个请求里就作用到搜索过滤
            body = client.patch(f"/api/images/{iid}", json={"category_id": cat["steak"]}).json()
            self.assertEqual((body["vector_sync"], body["image"]["scenes"], body["image"]["label_status"]),
                             ("pushed", ["general", "oven"], "verified"))
            self.assertEqual([hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")], [iid])
            self.assertEqual(vector(iid)["status"], "synced")
            body = client.patch(f"/api/images/{orphan}", json={"tags": ["x"]}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["image"]["tags"]), ("missing", True, ["x"]))

            # 已删除：从 PostgreSQL 消失，向量点和两个文件在同一个请求里删除
            body = client.delete(f"/api/images/{iid}").json()
            self.assertEqual((body["image_id"], body["cleanup"]), (iid, "done"))
            self.assertEqual(vectors.get_payloads([iid]), {})
            self.assertEqual(store.list_pending_deletions(), [])
            self.assertEqual(sorted(c.args[0] for c in storage.delete_by_url_or_key.call_args_list),
                             ["https://cdn/o/1.jpg", "https://cdn/t/1.webp"])
            self.assertEqual(client.get(f"/api/images/{iid}").status_code, 404)

    def test_a_value_added_to_the_enum_file_is_stored_and_blocks_downgrade(self):
        import shutil
        import tempfile

        from sqlalchemy.exc import IntegrityError

        from app.core.config.enum_store import EnumStore, set_enum_store
        from app.core.errors import AppError

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        enums_path = Path(tmp.name) / "enums.yaml"
        shutil.copyfile(BACKEND / "config" / "param" / "enums.yaml", enums_path)
        enum_file = EnumStore(enums_path)
        self.addCleanup(set_enum_store, set_enum_store(enum_file))

        store = self.store()
        with self.assertRaises(AppError) as ctx:
            self.insert(store, 1, food_state="thawed", source_type="partner_api")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

        enum_file.add_value("food_state", "thawed", "解冻")
        enum_file.add_value("source_type", "partner_api", "合作方接口")
        iid = self.insert(store, 1, food_state="thawed", source_type="partner_api")
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["food_state"], row["source_type"]), ("thawed", "partner_api"))

        # 0004 的降级会恢复旧的 CHECK 列表，所以存在这样的行时它会拒绝
        with self.assertRaisesRegex(IntegrityError, "ck_images_(source_type|food_state)"):
            _alembic("downgrade", "20261106_0003")
        self.sql("UPDATE images SET source_type = 'user_upload'")
        self.sql("UPDATE image_foods SET food_state = 'frozen'")  # 0006 的降级会把它复制回 images
        _alembic("downgrade", "20261106_0003")
        _alembic("upgrade", "head")

    def test_food_annotation_lives_in_its_own_optional_table(self):
        from sqlalchemy.exc import IntegrityError

        from app.core.errors import AppError
        from app.core.rules.food_fields import FOOD_FIELDS

        store = self.store()
        bare = self.insert(store, 1)  # 非必填: nothing annotated -> no image_foods row
        self.assertEqual(self.sql("SELECT count(*) FROM image_foods").scalar_one(), 0)
        self.assertEqual({k: store.get_image_by_uuid(bare)[k] for k in FOOD_FIELDS}, dict.fromkeys(FOOD_FIELDS))

        food = {"food_state": "raw", "food_processing": "sliced", "food_count": 4,
                "total_mass_g": 350, "water_mass_g": 0, "main_ingredient_mass_g": 300}
        iid = self.insert(store, 2, **food)
        row = store.get_image_by_uuid(iid)
        self.assertEqual({k: row[k] for k in FOOD_FIELDS}, food)
        before = row["payload_version"]

        # 修改只改变传来的列；这些不在 Qdrant payload 里：不递增
        store.patch_image(iid, {"total_mass_g": "1200", "food_count": None})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["total_mass_g"], row["food_count"], row["water_mass_g"], row["payload_version"]),
                         (1200, None, 0, before))
        store.patch_image(iid, {"food_state": "cooked"})  # food_state 在 payload 里
        self.assertEqual(store.get_image_by_uuid(iid)["payload_version"], before + 1)
        store.patch_image(bare, {"water_mass_g": 80})  # 图片的第一次标注会创建它的行
        self.assertEqual(store.get_image_by_uuid(bare)["water_mass_g"], 80)

        items, _ = store.list_images(scene="general")  # 列表 / 搜索通过同一个 join 补全信息
        self.assertEqual({i["id"]: i["total_mass_g"] for i in items}, {bare: None, iid: 1200})
        self.assertEqual(store.get_active_by_ids([iid])[iid]["main_ingredient_mass_g"], 300)

        with self.assertRaises(AppError) as ctx:
            store.patch_image(iid, {"main_ingredient_mass_g": 0})
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        # 对绕过仓储的写入方，数据库也保持同样的规则
        with self.assertRaisesRegex(IntegrityError, "ck_image_foods_total_mass_positive"):
            self.sql("UPDATE image_foods SET total_mass_g = 0")
        store.enqueue_image_deletion(iid)  # 删除图片会删除它的标注
        self.assertEqual(self.sql("SELECT count(*) FROM image_foods").scalar_one(), 1)

    def test_0006_moves_the_food_columns_into_image_foods_and_back(self):
        _alembic("downgrade", "20261109_0005")
        ids = [uuid.UUID(bytes=_sha(i)[:16]) for i in (1, 2)]
        self.sql(
            "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, source_type, license, "
            "label_status, food_state, food_processing, food_weight_g, food_count) VALUES "
            "(:a, :sa, 'https://x/o1', 'https://x/t1', 4, 3, 'user_upload', 'unknown', 'unlabeled', "
            " 'raw', 'sliced', 350, 4), "
            "(:b, :sb, 'https://x/o2', 'https://x/t2', 4, 3, 'user_upload', 'unknown', 'unlabeled', "
            " NULL, NULL, NULL, NULL)",
            {"a": ids[0], "sa": _sha(1), "b": ids[1], "sb": _sha(2)},
        )
        _alembic("upgrade", "head")
        rows = self.sql(
            "SELECT image_id, food_state, food_processing, food_count, total_mass_g, water_mass_g FROM image_foods"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [(ids[0], "raw", "sliced", 4, 350, None)])  # 重量 -> 总质量

        _alembic("downgrade", "20261109_0005")
        self.assertEqual(dict(self.sql("SELECT id, food_weight_g FROM images").fetchall()), {ids[0]: 350, ids[1]: None})
        _alembic("upgrade", "head")

    def test_description_round_trip_without_payload_bump(self):
        store = self.store()
        default_id = self.insert(store, 1)
        self.assertEqual(store.get_image_by_uuid(default_id)["description"], "")
        iid = self.insert(store, 2, description="  酱色红烧肉 ")
        self.assertEqual(store.get_image_by_uuid(iid)["description"], "酱色红烧肉")
        before = store.get_image_by_uuid(iid)["payload_version"]
        store.patch_image(iid, {"description": "改过的描述"})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["description"], row["payload_version"]), ("改过的描述", before))
        items, _ = store.list_images(scene="general")
        self.assertEqual({i["id"]: i["description"] for i in items}, {default_id: "", iid: "改过的描述"})

    def test_tags_round_trip_filter_and_q_without_payload_bump(self):
        store = self.store()
        untagged = self.insert(store, 1)
        self.assertEqual(store.get_image_by_uuid(untagged)["tags"], [])
        iid = self.insert(store, 2, tags=[" 红薯叶 ", "带皮", "红薯叶"])
        self.assertEqual(store.get_image_by_uuid(iid)["tags"], ["红薯叶", "带皮"])

        def ids(**kw):
            return sorted(i["id"] for i in store.list_images(scene="general", **kw)[0])

        self.assertEqual(ids(tag="红薯叶"), [iid])
        self.assertEqual(ids(tag="红薯"), [])  # tag 是精确匹配
        self.assertEqual(ids(q="薯"), [iid])  # q 以子串方式匹配任一标签
        self.assertEqual(store.list_images(scene="general", tag="带皮")[1], 1)  # 总数遵循过滤条件

        before = store.get_image_by_uuid(iid)["payload_version"]
        store.patch_image(iid, {"tags": ["清蒸", " "]})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["tags"], row["payload_version"]), (["清蒸"], before))
        self.assertEqual((ids(tag="红薯叶"), ids(tag="清蒸")), ([], [iid]))
        self.assertEqual(store.get_active_by_ids([iid], scene="general")[iid]["tags"], ["清蒸"])  # 搜索补全信息

        store.patch_image(iid, {"tags": []})
        self.assertEqual(store.get_image_by_uuid(iid)["tags"], [])

    # ---- 类别树管理（app.repositories.categories）---------------

    @staticmethod
    def zh(name):
        return {"lang": "zh", "name": name, "kind": "primary"}

    def assert_category_error(self, code, fn, *args):
        from app.core.errors import AppError

        with self.assertRaises(AppError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code, ctx.exception.detail)
        return ctx.exception.detail

    def seed_tree(self, store):
        """ingredient（排在最前），dish > dish.pizza > dish.pizza.slice"""
        dish = store.create_category({"code": "dish", "names": [self.zh("菜品")]})
        ingredient = store.create_category({"code": "ingredient", "names": [self.zh("食材")], "sort_order": -1})
        pizza = store.create_category({
            "code": "dish.pizza",
            "parent_id": dish["id"],
            "names": [
                {"lang": "en", "name": "Pizza", "kind": "primary"},
                {"lang": "zh", "name": "比萨", "kind": "alias"},
                self.zh("披萨"),
            ],
            "cuisines": ["italian", "american", "italian"],
        })
        piece = store.create_category(
            {"code": "dish.pizza.slice", "parent_id": pizza["id"], "names": [self.zh("披萨切片")]}
        )
        return dish, ingredient, pizza, piece

    def test_category_create_builds_paths_names_and_a_display_order(self):
        store = self.store()
        self.assertEqual(store.list_categories(), [])
        dish, ingredient, pizza, piece = self.seed_tree(store)

        self.assertEqual((dish["parent_id"], dish["path"], dish["name"]), (None, str(dish["id"]), "菜品"))
        self.assertEqual((pizza["parent_id"], pizza["path"]), (dish["id"], f"{dish['id']}.{pizza['id']}"))
        self.assertEqual(piece["path"], f"{dish['id']}.{pizza['id']}.{piece['id']}")
        # names：先是显示语言的，同一种语言里主名称在前
        self.assertEqual(pizza["names"], [
            self.zh("披萨"),
            {"lang": "zh", "name": "比萨", "kind": "alias"},
            {"lang": "en", "name": "Pizza", "kind": "primary"},
        ])
        self.assertEqual((pizza["name"], pizza["cuisines"]), ("披萨", ["italian", "american"]))
        self.assertEqual(
            (pizza["sort_order"], pizza["is_active"], pizza["heating_group"], pizza["image_count"], pizza["scene_rule_count"]),
            (0, True, None, 0, 0),
        )
        from datetime import datetime

        self.assertIsNotNone(datetime.fromisoformat(pizza["created_at"]).tzinfo)  # 带偏移量的 ISO 8601

        # 父节点在子节点之前；同级按 sort_order，然后按 id
        listed = store.list_categories()
        self.assertEqual([n["code"] for n in listed], ["ingredient", "dish", "dish.pizza", "dish.pizza.slice"])
        self.assertEqual(listed[2], pizza)  # 写入返回的节点和列表显示的相同
        self.assertEqual(store.get_category_id_by_code("dish.pizza"), pizza["id"])  # 入库时解析它

    def test_category_patch_renames_relabels_and_replaces_names(self):
        store = self.store()
        dish, _, pizza, piece = self.seed_tree(store)
        hg = self.sql("INSERT INTO heating_groups (code, name) VALUES ('grill', '烤') RETURNING id").scalar_one()
        self.assertEqual(store.list_heating_groups(), [{"id": hg, "code": "grill", "name": "烤"}])
        self.sql("UPDATE categories SET updated_at = now() - interval '1 day' WHERE id = :id", {"id": pizza["id"]})
        stale = store.list_categories()[2]["updated_at"]

        patched = store.update_category(pizza["id"], {
            "code": "dish.pizza_pie", "names": [self.zh("意式披萨")], "cuisines": [],
            "heating_group_id": hg, "sort_order": 7, "is_active": False,
        })["category"]
        self.assertEqual(
            (patched["code"], patched["name"], patched["names"], patched["cuisines"]),
            ("dish.pizza_pie", "意式披萨", [self.zh("意式披萨")], []),
        )
        self.assertEqual(patched["heating_group"], {"id": hg, "code": "grill", "name": "烤"})
        self.assertEqual((patched["sort_order"], patched["is_active"], patched["path"]), (7, False, pizza["path"]))
        by_id = {node["id"]: node for node in store.list_categories()}
        self.assertEqual(by_id[piece["id"]]["code"], "dish.pizza_pie.slice")
        self.assertGreater(patched["updated_at"], stale)  # 原生 SQL 要自己设置它
        self.assertEqual(
            self.sql("SELECT count(*) FROM category_names WHERE category_id = :c", {"c": pizza["id"]}).scalar_one(), 1
        )

        # 只修改传来的字段；null 清空加热组
        cleared = store.update_category(pizza["id"], {"heating_group_id": None})["category"]
        self.assertIsNone(cleared["heating_group"])
        self.assertEqual((cleared["code"], cleared["sort_order"], cleared["names"]), ("dish.pizza_pie", 7, [self.zh("意式披萨")]))
        # 只修改名称的请求也会更新 updated_at
        self.sql("UPDATE categories SET updated_at = now() - interval '1 day' WHERE id = :id", {"id": dish["id"]})
        before = store.list_categories()[1]["updated_at"]
        self.assertGreater(store.update_category(dish["id"], {"names": [self.zh("菜肴")]})["category"]["updated_at"], before)

    def test_category_move_takes_the_subtree_and_respects_the_tree_rules(self):
        store = self.store()
        dish, ingredient, pizza, piece = self.seed_tree(store)

        moved = store.update_category(pizza["id"], {"parent_id": ingredient["id"]})["category"]
        self.assertEqual((moved["parent_id"], moved["path"]), (ingredient["id"], f"{ingredient['id']}.{pizza['id']}"))
        self.assertEqual(moved["code"], "ingredient.pizza")
        by_code = {n["code"]: n for n in store.list_categories()}
        self.assertEqual(by_code["ingredient.pizza.slice"]["path"], f"{ingredient['id']}.{pizza['id']}.{piece['id']}")
        self.assertEqual(by_code["ingredient.pizza.slice"]["parent_id"], pizza["id"])

        update = store.update_category
        self.assertIn("descendants", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": piece["id"]}))
        self.assertIn("descendants", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": pizza["id"]}))
        self.assertIn("does not exist", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": 987654}))
        # 只有领域可以是根节点：无论是创建、向上移动，还是重命名根节点
        create = store.create_category
        self.assertIn("needs a parent", self.assert_category_error("VALIDATION_ERROR", create, {"code": "snack", "names": [self.zh("零食")]}))
        self.assertIn("needs a parent", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": None}))
        self.assert_category_error("VALIDATION_ERROR", update, dish["id"], {"code": "dishes"})
        # ……而且在另一个节点下的领域可以移回去
        dessert = create({"code": "dessert", "parent_id": dish["id"], "names": [self.zh("甜点烘焙")]})
        self.assertEqual(update(dessert["id"], {"parent_id": None})["category"]["path"], str(dessert["id"]))
        # 上面没有任何东西只应用了一半
        self.assertEqual(
            {n["code"]: n["parent_id"] for n in store.list_categories()},
            {"ingredient": None, "dish": None, "dessert": None, "ingredient.pizza": ingredient["id"], "ingredient.pizza.slice": pizza["id"]},
        )

    def test_category_subtree_move_conflict_keeps_all_codes_paths_and_parents(self):
        store = self.store()
        _, ingredient, pizza, _ = self.seed_tree(store)
        store.create_category({
            "code": "INGREDIENT.PIZZA.SLICE", "parent_id": ingredient["id"], "names": [self.zh("冲突节点")],
        })
        before = self.sql("SELECT id, code, parent_id, path::text FROM categories ORDER BY id").fetchall()
        self.assert_category_error("CATEGORY_CODE_EXISTS", store.update_category, pizza["id"], {"parent_id": ingredient["id"]})
        self.assertEqual(self.sql("SELECT id, code, parent_id, path::text FROM categories ORDER BY id").fetchall(), before)

    def test_category_subtree_recode_can_reuse_a_descendants_previous_code(self):
        store = self.store()
        _, _, pizza, piece = self.seed_tree(store)
        # 单条 UPDATE 的顺序会撞上旧子节点 code 上的即时唯一索引。
        store.update_category(pizza["id"], {"code": "dish.pizza.slice"})
        nodes = {node["id"]: node for node in store.list_categories()}
        self.assertEqual(nodes[pizza["id"]]["code"], "dish.pizza.slice")
        self.assertEqual(nodes[piece["id"]]["code"], "dish.pizza.slice.slice")
        self.assertEqual(nodes[pizza["id"]]["path"], pizza["path"])
        self.assertEqual(nodes[piece["id"]]["path"], piece["path"])

    def test_category_codes_are_unique_and_references_must_exist(self):
        store = self.store()
        dish, _, pizza, _ = self.seed_tree(store)
        duplicate = {"code": "dish.pizza", "parent_id": dish["id"], "names": [self.zh("重复")]}
        self.assertIn("'dish.pizza'", self.assert_category_error("CATEGORY_CODE_EXISTS", store.create_category, duplicate))
        self.assert_category_error("CATEGORY_CODE_EXISTS", store.update_category, pizza["id"], {"code": "dish"})
        self.assertEqual(store.update_category(pizza["id"], {"code": "dish.pizza"})["category"]["code"], "dish.pizza")  # 它自己的 code 没问题

        self.assert_category_error("VALIDATION_ERROR", store.create_category, dict(duplicate, code="dish.x", parent_id=987654))
        self.assert_category_error("VALIDATION_ERROR", store.create_category, dict(duplicate, code="dish.x", heating_group_id=987654))
        self.assert_category_error("VALIDATION_ERROR", store.update_category, pizza["id"], {"heating_group_id": 987654})
        self.assert_category_error("CATEGORY_NOT_FOUND", store.update_category, 987654, {"sort_order": 1})
        self.assert_category_error("CATEGORY_NOT_FOUND", store.delete_category, 987654)
        self.assertEqual(self.sql("SELECT count(*) FROM categories").scalar_one(), 4)

    def test_category_codes_preserve_case_and_are_unique_ignoring_case(self):
        from sqlalchemy.exc import IntegrityError

        store = self.store()
        root = store.create_category({"code": "Ingredient", "names": [self.zh("食材")]})
        sweet = store.create_category({"code": "ingredient.HongSu", "parent_id": root["id"], "names": [self.zh("红薯")]})
        other = store.create_category({"code": "ingredient.ZiShuu", "parent_id": root["id"], "names": [self.zh("紫薯")]})
        self.assertEqual((root["code"], sweet["code"]), ("Ingredient", "ingredient.HongSu"))
        self.assertEqual(store.get_category_id_by_code("INGREDIENT.HONGSU"), sweet["id"])
        self.assertEqual(store.get_category_id_by_code("ingredient.hongsu"), sweet["id"])
        self.assert_category_error("CATEGORY_CODE_EXISTS", store.create_category, {
            "code": "Ingredient.hONGsU", "parent_id": root["id"], "names": [self.zh("重复")],
        })
        self.assert_category_error("CATEGORY_CODE_EXISTS", store.update_category, other["id"], {"code": "Ingredient.hONGsU"})
        renamed = store.update_category(sweet["id"], {"code": "Ingredient.hONGsU"})["category"]
        self.assertEqual((renamed["code"], renamed["id"]), ("Ingredient.hONGsU", sweet["id"]))
        # 仓储之外的写入方仍然会撞上 lower(code) 唯一索引。
        with self.assertRaises(IntegrityError):
            self.sql("INSERT INTO categories (code) VALUES ('INGREDIENT.HONGSU')")
        with self.assertRaises(IntegrityError):
            self.sql("UPDATE categories SET code = 'ingredient.hongsu' WHERE id = :id", {"id": other["id"]})

    def test_category_code_case_unique_migration_reports_conflicts_without_changing_rows(self):
        _alembic("downgrade", "20261112_0009")
        ids = [self.sql("INSERT INTO categories (code) VALUES (:code) RETURNING id", {"code": code}).scalar_one()
               for code in ("LegacyCase", "legacycase")]
        with self.assertRaises(Exception) as caught:
            _alembic("upgrade", "head")
        error = str(caught.exception)
        self.assertIn("Category codes conflict ignoring case", error)
        self.assertIn("LegacyCase", error)
        self.assertIn("legacycase", error)
        self.assertEqual(self.sql("SELECT code FROM categories ORDER BY id").scalars().all(), ["LegacyCase", "legacycase"])
        self.assertEqual(self.sql("SELECT version_num FROM alembic_version").scalar_one(), "20261112_0009")
        self.sql("UPDATE categories SET code = 'LegacyOther' WHERE id = :id", {"id": ids[1]})
        _alembic("upgrade", "head")
        self.assertEqual(self.sql("SELECT version_num FROM alembic_version").scalar_one(), "20261113_0010")

    def test_category_inserted_by_sql_keeps_its_root_code_until_it_is_renamed(self):
        store = self.store()
        legacy = self.sql("INSERT INTO categories (code) VALUES ('legacy') RETURNING id").scalar_one()
        node = store.list_categories()[0]
        self.assertEqual((node["code"], node["name"], node["names"]), ("legacy", None, []))
        self.assertEqual(store.update_category(legacy, {"sort_order": 9, "names": [self.zh("旧根")]})["category"]["name"], "旧根")
        self.assert_category_error("VALIDATION_ERROR", store.update_category, legacy, {"code": "legacy2"})
        self.assertEqual(store.update_category(legacy, {"code": "beverage"})["category"]["code"], "beverage")

    def test_category_root_cap_counts_legacy_and_inactive_roots_and_allows_freeing_a_slot(self):
        store = self.store()
        self.sql("INSERT INTO categories (code) VALUES ('legacy')")
        roots = {
            code: store.create_category({"code": code, "names": [self.zh(code)]})
            for code in ("ingredient", "dish", "dessert", "beverage", "mixed")
        }
        self.assertEqual(self.sql("SELECT count(*) FROM categories WHERE parent_id IS NULL").scalar_one(), 6)
        # 旧的 code 即使不在允许的六个 code 之内也占一个名额。
        other_fields = {"code": "other", "names": [self.zh("其他")]}
        self.assertIn("at most 6", self.assert_category_error("VALIDATION_ERROR", store.create_category, other_fields))
        store.update_category(roots["mixed"]["id"], {"is_active": False})
        # 停用不会释放根节点名额；重复的 code 保持冲突错误。
        self.assertIn("at most 6", self.assert_category_error("VALIDATION_ERROR", store.create_category, other_fields))
        self.assert_category_error(
            "CATEGORY_CODE_EXISTS", store.create_category, {"code": "mixed", "names": [self.zh("重复")]}
        )
        other = store.create_category({**other_fields, "parent_id": roots["ingredient"]["id"]})
        self.assertIn(
            "at most 6", self.assert_category_error("VALIDATION_ERROR", store.update_category, other["id"], {"parent_id": None})
        )
        self.assertEqual(
            next(n for n in store.list_categories() if n["id"] == other["id"])["parent_id"],
            roots["ingredient"]["id"],
        )
        # 把已有的根节点移到另一个类别下面，正好释放一个名额。
        store.update_category(roots["mixed"]["id"], {"parent_id": roots["ingredient"]["id"]})
        promoted = store.update_category(other["id"], {"parent_id": None})["category"]
        self.assertEqual((promoted["parent_id"], promoted["path"]), (None, str(other["id"])))
        self.assertEqual(self.sql("SELECT count(*) FROM categories WHERE parent_id IS NULL").scalar_one(), 6)

    def test_category_delete_refuses_a_node_in_use_and_removes_a_free_one(self):
        store = self.store()
        dish, ingredient, pizza, piece = self.seed_tree(store)
        delete = store.delete_category

        self.assertIn("child categories: 1", self.assert_category_error("CATEGORY_IN_USE", delete, pizza["id"]))
        image = self.insert(store, 1, category_id=piece["id"], label_status="auto")
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        self.sql("INSERT INTO scene_rules (scene_id, category_id) VALUES (:s, :c)", {"s": oven, "c": piece["id"]})
        counts = {n["code"]: (n["image_count"], n["scene_rule_count"]) for n in store.list_categories()}
        self.assertEqual(counts, {"ingredient": (0, 0), "dish": (0, 0), "dish.pizza": (0, 0), "dish.pizza.slice": (1, 1)})
        detail = self.assert_category_error("CATEGORY_IN_USE", delete, piece["id"])
        self.assertIn("images: 1", detail)
        self.assertIn("scene rules: 1", detail)
        self.assertEqual(self.sql("SELECT count(*) FROM scene_rules").scalar_one(), 1)  # 规则没有被级联删除

        self.sql("DELETE FROM scene_rules")
        self.sql("DELETE FROM images WHERE id = :id", {"id": image})
        self.assertEqual(delete(piece["id"]), {"id": piece["id"], "code": "dish.pizza.slice"})
        self.assertEqual(
            self.sql("SELECT count(*) FROM category_names WHERE category_id = :c", {"c": piece["id"]}).scalar_one(), 0
        )
        self.assertEqual(delete(pizza["id"])["code"], "dish.pizza")  # 没有子节点了
        self.assertEqual([n["code"] for n in store.list_categories()], ["ingredient", "dish"])

    # ---- 场景配置（app.repositories.scene_config / scenes）-----

    def assert_app_error(self, code, fn, *args, **kwargs):
        from app.core.errors import AppError

        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code, ctx.exception.detail)
        return ctx.exception.detail

    def test_scene_requests_follow_the_scenes_table(self):
        from app.core import errors as err
        from app.services.retrieval.list_images import ListImagesUseCase
        from app.services.retrieval.scenes import lookup_scene, requested_scene_code, resolve_index_scenes

        store = self.store()
        image = self.insert(store, 1)
        list_uc = ListImagesUseCase(store)
        self.assertEqual(list_uc.execute()["scene"], "general")  # 默认场景
        self.assertEqual(list_uc.execute(scene="normal")["scene"], "general")  # MVP 的 code 仍然会映射
        detail = self.assert_app_error("LIST_UNKNOWN_SCENE", list_uc.execute, scene="steam_oven")
        self.assertIn("general, oven, range_hood, us_diet, cn_diet, jp_diet", detail)  # 告诉客户端有哪些

        # 通过配置创建的场景立刻在所有地方可用
        store.create_scene({"code": "steam_oven", "name": "蒸烤箱", "include_all": True})
        self.assertEqual((list_uc.execute(scene="steam_oven")["total"]), 1)
        self.assertEqual(resolve_index_scenes(store, scenes=["steam_oven", "general"]), ["steam_oven", "general"])
        self.assertEqual(
            lookup_scene(store, requested_scene_code(" steam_oven "), unknown=err.search_unknown_scene,
                         failed=err.search_postgres_failed)["code"],
            "steam_oven",
        )
        # ……停用后就被拒绝
        steam = store.get_scene_by_code("steam_oven")["id"]
        store.update_scene(steam, {"is_active": False})
        self.assertIn("is inactive", self.assert_app_error("LIST_UNKNOWN_SCENE", list_uc.execute, scene="steam_oven"))
        self.assert_app_error("INDEX_UNKNOWN_SCENE", resolve_index_scenes, store, scene="steam_oven")
        self.assertEqual(store.get_effective_scene_codes(image), ["general"])

    def test_an_inactive_scene_has_no_members_anywhere(self):
        cat = self.seed_catalog()
        store = self.store()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        oven = store.get_scene_by_code("oven")["id"]
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

        store.update_scene(oven, {"is_active": False})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])
        self.assertEqual(store.list_images(scene="oven"), ([], 0))
        self.assertEqual(store.get_active_by_ids([steak], scene="oven"), {})
        self.assertEqual(store.get_payload_rows([steak])[0]["scenes"], ["general"])  # 同步会推送的内容
        oven_row = next(s for s in store.list_scene_configs() if s["code"] == "oven")
        self.assertEqual(oven_row["member_count"], 0)

        store.update_scene(oven, {"is_active": True})  # 行保留着：成员立刻回来
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

    def test_scene_configuration_crud_and_its_guards(self):
        cat = self.seed_catalog()
        store = self.store()
        general = store.get_scene_by_code("general")["id"]
        self.assertIn("cannot be deleted", self.assert_app_error("SCENE_PROTECTED", store.delete_scene, general))
        self.assert_app_error("SCENE_PROTECTED", store.update_scene, general, {"is_active": False})
        self.assert_app_error("SCENE_PROTECTED", store.update_scene, general, {"include_all": False})
        self.assertEqual(store.update_scene(general, {"name": "通用", "description": "  "})["description"], None)

        created = store.create_scene({"code": "steam_oven", "name": "蒸烤箱", "outputs_heating": True})
        self.assertEqual(
            {k: created[k] for k in ("code", "name", "include_all", "outputs_heating", "is_active", "member_count", "labels", "rules")},
            {"code": "steam_oven", "name": "蒸烤箱", "include_all": False, "outputs_heating": True, "is_active": True,
             "member_count": 0, "labels": [], "rules": []},
        )
        sid = created["id"]
        self.assert_app_error("SCENE_CODE_EXISTS", store.create_scene, {"code": "steam_oven", "name": "重复"})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene, {"code": "device", "name": "旧编码"})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene, sid, {"code": "steam"})

        # 标签：每个场景内唯一，只有没有规则使用时才能删除
        scene = store.create_scene_label(sid, {"code": "steak", "name": "牛排", "sort_order": 2})
        scene = store.create_scene_label(sid, {"code": "meat", "name": "肉类", "sort_order": 1})
        self.assertEqual([l["code"] for l in scene["labels"]], ["meat", "steak"])  # sort_order
        steak_label = next(l["id"] for l in scene["labels"] if l["code"] == "steak")
        meat_label = next(l["id"] for l in scene["labels"] if l["code"] == "meat")
        self.assert_app_error("SCENE_CODE_EXISTS", store.create_scene_label, sid, {"code": "steak", "name": "又一个"})
        self.assert_app_error("SCENE_CODE_EXISTS", store.update_scene_label, sid, meat_label, {"code": "steak"})
        oven_label = self.sql("SELECT id FROM scene_labels WHERE code = 'steak' AND scene_id <> :s", {"s": sid}).scalar_one()
        self.assert_app_error("SCENE_NOT_FOUND", store.update_scene_label, sid, oven_label, {"name": "别的场景的"})

        # 规则：每个类别 / 菜系一条；标签只能是本场景的，并且只能用在类别规则上
        scene = store.create_scene_rule(sid, {"category_id": cat["steak"], "label_id": steak_label})
        scene = store.create_scene_rule(sid, {"cuisine": "american"})
        self.assertEqual(
            [(r["category"]["code"] if r["category"] else None, r["cuisine"], r["label"]["code"] if r["label"] else None) for r in scene["rules"]],
            [("dish.steak", None, "steak"), (None, "american", None)],
        )
        self.assertEqual(scene["rules"][0]["category"]["name"], "牛排")
        self.assert_app_error("SCENE_RULE_EXISTS", store.create_scene_rule, sid, {"category_id": cat["steak"]})
        self.assert_app_error("SCENE_RULE_EXISTS", store.create_scene_rule, sid, {"cuisine": "american"})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"category_id": cat["dish"], "label_id": oven_label})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"category_id": 987654})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"cuisine": "chinese", "label_id": meat_label})
        cuisine_rule = scene["rules"][1]["id"]
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, sid, cuisine_rule, {"label_id": meat_label})
        self.assert_app_error("SCENE_IN_USE", store.delete_scene_label, sid, steak_label)

        category_rule = scene["rules"][0]["id"]
        scene = store.update_scene_rule(sid, category_rule, {"label_id": meat_label})
        self.assertEqual(scene["rules"][0]["label"]["code"], "meat")
        self.assertEqual({l["code"]: l["rule_count"] for l in scene["labels"]}, {"meat": 1, "steak": 0})
        scene = store.delete_scene_label(sid, steak_label)
        self.assertEqual([l["code"] for l in scene["labels"]], ["meat"])
        scene = store.delete_scene_rule(sid, cuisine_rule)
        self.assertEqual(len(scene["rules"]), 1)
        self.assert_app_error("SCENE_NOT_FOUND", store.delete_scene_rule, sid, cuisine_rule)

        # 类别管理把规则视为一种使用
        self.assertEqual(next(n for n in store.list_categories() if n["code"] == "dish.steak")["scene_rule_count"], 1)

        # 删除场景会带走它的标签、规则和归属；它的 code 还在成员的 payload 里，
        # 所以删除会要求同步
        image = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(image), ["general", "oven", "steam_oven"])
        self.assertEqual(store.delete_scene(sid), {"id": sid, "code": "steam_oven", "sync_required": True})
        empty = store.create_scene({"code": "air_fryer", "name": "空气炸锅"})
        self.assertEqual(store.delete_scene(empty["id"])["sync_required"], False)  # 从来没有成员
        for table in ("scene_labels", "scene_rules", "image_scenes"):
            self.assertEqual(self.sql(f"SELECT count(*) FROM {table} WHERE scene_id = :s", {"s": sid}).scalar_one(), 0, table)
        self.assertEqual(store.get_effective_scene_codes(image), ["general", "oven"])
        self.assert_app_error("SCENE_NOT_FOUND", store.update_scene, sid, {"name": "x"})

    def test_writes_say_whether_stored_images_need_a_sync(self):
        cat = self.seed_catalog()
        store = self.store()

        def flag(result):
            return result["sync_required"]

        # 新的按规则的场景还没有成员；新的启用的 include_all 场景包含所有图片
        rule_based = store.create_scene({"code": "steam_oven", "name": "蒸烤箱"})
        sid = rule_based["id"]
        self.assertFalse(flag(rule_based))
        self.assertTrue(flag(store.create_scene({"code": "air_fryer", "name": "空气炸锅", "include_all": True})))
        self.assertFalse(flag(store.create_scene({"code": "grill", "name": "烤架", "include_all": True, "is_active": False})))

        # 每次请求时读取：名称、描述、outputs_heating、标签
        self.assertFalse(flag(store.update_scene(sid, {"name": "蒸烤箱 2", "description": "x", "outputs_heating": True})))
        self.assertFalse(flag(store.update_scene(sid, {"is_active": True, "include_all": False})))  # 值没有变
        scene = store.create_scene_label(sid, {"code": "steak", "name": "牛排"})
        self.assertFalse(flag(scene))
        label = scene["labels"][0]["id"]
        self.assertFalse(flag(store.update_scene_label(sid, label, {"name": "牛排 2", "is_active": False})))

        # 启用的按规则场景的规则会改变归属；标签映射不会
        scene = store.create_scene_rule(sid, {"category_id": cat["dish"]})
        self.assertTrue(flag(scene))
        rule = scene["rules"][0]["id"]
        self.assertFalse(flag(store.update_scene_rule(sid, rule, {"label_id": label})))
        self.assertFalse(flag(store.update_scene_rule(sid, rule, {"category_id": cat["dish"], "cuisine": None})))  # 同一个对象
        self.assertTrue(flag(store.update_scene_rule(sid, rule, {"category_id": cat["steak"]})))
        self.assertFalse(flag(store.delete_scene_label(sid, store.create_scene_label(sid, {"code": "x", "name": "x"})["labels"][-1]["id"])))

        # 关闭场景或改成 include_all 会改变归属；关闭期间什么都不会改变
        self.assertTrue(flag(store.update_scene(sid, {"is_active": False})))
        self.assertFalse(flag(store.create_scene_rule(sid, {"cuisine": "american"})))
        self.assertFalse(flag(store.update_scene(sid, {"include_all": True})))  # 仍然关闭
        self.assertTrue(flag(store.update_scene(sid, {"is_active": True})))  # 开启，包含所有图片
        self.assertFalse(flag(store.delete_scene_rule(sid, rule)))  # include_all 场景忽略它的规则
        self.assertTrue(flag(store.update_scene(sid, {"include_all": False})))

    def test_a_rule_target_changes_in_place(self):
        cat = self.seed_catalog()  # oven：针对 dish 的规则，标签为 steak
        store = self.store()
        oven = store.get_scene_by_code("oven")["id"]
        label = cat["label"]
        rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()

        def rules():
            return [(r["id"], r["category"]["code"] if r["category"] else None, r["cuisine"], r["label"]["code"] if r["label"] else None)
                    for r in next(s for s in store.list_scene_configs() if s["id"] == oven)["rules"]]

        # 另一个类别：规则 id 相同，标签保留
        store.update_scene_rule(oven, rule, {"category_id": cat["steak"]})
        self.assertEqual(rules(), [(rule, "dish.steak", None, "steak")])

        # 菜系规则不能保留标签：被拒绝（什么都不变），直到标签也被清空
        detail = self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"cuisine": "american"})
        self.assertIn("label_id: null", detail)
        self.assertEqual(rules(), [(rule, "dish.steak", None, "steak")])
        result = store.update_scene_rule(oven, rule, {"cuisine": "american", "label_id": None})
        self.assertTrue(result["sync_required"])
        self.assertEqual(rules(), [(rule, None, "american", None)])

        # 在一个请求里改回类别，并带上标签
        store.update_scene_rule(oven, rule, {"category_id": cat["dish"], "label_id": label})
        self.assertEqual(rules(), [(rule, "dish", None, "steak")])

        # 对象在这个场景里必须没有被占用，类别必须存在，恰好一个对象
        other = store.create_scene_rule(oven, {"cuisine": "american"})["rules"][-1]["id"]
        self.assert_app_error("SCENE_RULE_EXISTS", store.update_scene_rule, oven, other, {"category_id": cat["dish"]})
        self.assert_app_error("SCENE_RULE_EXISTS", store.update_scene_rule, oven, rule, {"cuisine": "american", "label_id": None})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": 987654})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": cat["dish"], "cuisine": "x"})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": None})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {})
        self.assertEqual(rules(), [(rule, "dish", None, "steak"), (other, None, "american", None)])

        # 已存图片在下一次重算时跟随新的对象
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])
        store.delete_scene_rule(oven, other)
        store.update_scene_rule(oven, rule, {"cuisine": "chinese", "label_id": None})
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])

    def test_the_ledger_records_only_current_payload_versions(self):
        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_ledger', 'current') RETURNING id"
        ).scalar_one()
        a, b = self.insert(store, 1), self.insert(store, 2)
        self.sql("UPDATE images SET payload_version = 3 WHERE id = :i", {"i": uuid.UUID(b)})
        gone = str(uuid.uuid4())  # 这期间被删除了：没有行，也不会外键失败

        recorded = store.mark_payloads_pushed(model_id=model_id, versions={a: 1, b: 2, gone: 1})
        self.assertEqual(recorded, [a])  # b 的 payload 被读取之后它变成了 3
        self.assertEqual(store.mark_payloads_pushed(model_id=model_id, versions={b: 3}), [b])
        self.assertEqual(store.mark_payloads_pushed(model_id=model_id, versions={}), [])
        ledger = dict(self.sql("SELECT image_id::text, synced_version FROM image_embeddings").fetchall())
        self.assertEqual(ledger, {a: 1, b: 3})

    def test_batch_recompute_follows_the_rules_and_bumps_only_what_changed(self):
        cat = self.seed_catalog()  # oven：针对 dish（菜系 {american}）的规则，标签为 steak
        store = self.store()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        plain = self.insert(store, 2)
        us_diet = store.get_scene_by_code("us_diet")["id"]
        oven = store.get_scene_by_code("oven")["id"]

        def versions():
            return dict(self.sql("SELECT id::text, payload_version FROM images").fetchall())

        # 新规则在已存图片被重算之前对它们没有影响
        store.create_scene_rule(us_diet, {"cuisine": "american"})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

        # 按 id 顺序分页；游标以 None 结束
        ordered = sorted([steak, plain])
        first = store.recompute_scene_memberships(limit=1)
        self.assertEqual((first["image_ids"], first["last_id"]), ([ordered[0]], ordered[0]))
        second = store.recompute_scene_memberships(after=first["last_id"], limit=1)
        self.assertEqual(second["image_ids"], [ordered[1]])
        self.assertEqual(store.recompute_scene_memberships(after=second["last_id"], limit=1),
                         {"image_ids": [], "changed_ids": [], "last_id": None})
        self.assertEqual(sorted(first["changed_ids"] + second["changed_ids"]), [steak])
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven", "us_diet"])  # 菜系继承自 dish
        self.assertEqual(versions(), {steak: 2, plain: 1})

        # 什么都没变：第二遍不递增任何图片
        self.assertEqual(store.recompute_scene_memberships(limit=10)["changed_ids"], [])
        self.assertEqual(versions(), {steak: 2, plain: 1})

        # 删除规则会删除它的成员；手工覆盖在重算后保留
        store.set_manual_scene_membership(plain, "oven", is_member=True)
        oven_rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()
        store.delete_scene_rule(oven, oven_rule)
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "us_diet"])
        self.assertEqual(store.get_effective_scene_codes(plain), ["general", "oven"])
        rows = self.sql("SELECT origin, count(*) FROM image_scenes GROUP BY origin ORDER BY origin").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("manual", 1), ("rule", 1)])

        # 移动类别会以同样的方式改变它子树的归属
        store.create_scene_rule(oven, {"category_id": cat["dish"]})
        ingredient = store.create_category({"code": "ingredient", "names": [self.zh("食材")]})
        store.update_category(cat["steak"], {"parent_id": ingredient["id"]})  # 不再在 dish 下面
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])  # 也不再是 american

    def test_a_category_move_or_cuisines_change_recomputes_the_images_below_it(self):
        cat = self.seed_catalog()  # oven：针对 dish 的规则；dish 的菜系为 {american}；steak 在 dish 下面
        store = self.store()
        store.create_scene_rule(store.get_scene_by_code("us_diet")["id"], {"cuisine": "american"})
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        dish_img = self.insert(store, 2, category_id=cat["dish"], label_status="auto")
        plain = self.insert(store, 3)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven", "us_diet"])

        def versions():
            return dict(self.sql("SELECT id::text, payload_version FROM images").fetchall())

        def update(category_id, fields):
            return store.update_category(category_id, fields)["scene_membership"]

        start = versions()
        # 名称、顺序和没变的菜系集合不决定任何场景：不重算任何东西
        self.assertIsNone(update(cat["steak"], {"names": [self.zh("西冷牛排")], "sort_order": 3}))
        self.assertIsNone(update(cat["dish"], {"cuisines": ["american", "american"]}))
        self.assertEqual(versions(), start)

        # 菜系会作用到子树里所有继承它的图片；id 按 id 顺序
        both = sorted([steak, dish_img])
        self.assertEqual(update(cat["dish"], {"cuisines": []}), {"images": 2, "changed_ids": both})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])
        self.assertEqual(update(cat["dish"], {"cuisines": ["american"]}), {"images": 2, "changed_ids": both})
        self.assertEqual(store.get_effective_scene_codes(dish_img), ["general", "oven", "us_diet"])

        # 把 steak 移出 dish 就离开了 dish 的规则，以及它从 dish 继承的菜系
        ingredient = store.create_category({"code": "ingredient", "names": [self.zh("食材")]})
        result = store.update_category(cat["steak"], {"parent_id": ingredient["id"]})
        self.assertEqual(result["category"]["parent_id"], ingredient["id"])
        self.assertEqual(result["scene_membership"], {"images": 1, "changed_ids": [steak]})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])
        self.assertEqual(update(ingredient["id"], {"cuisines": ["american"]}), {"images": 1, "changed_ids": [steak]})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "us_diet"])

        # 手工覆盖保留：回到 dish 下面后，steak 跟随 oven 的规则，但仍然不在 us_diet 里
        store.set_manual_scene_membership(steak, "us_diet", is_member=False)
        self.assertEqual(update(cat["steak"], {"parent_id": cat["dish"]}), {"images": 1, "changed_ids": [steak]})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

        # 每次修改都让图片递增了一次；子树之外的图片不受影响
        self.assertEqual(versions(), {steak: start[steak] + 6, dish_img: start[dish_img] + 2, plain: start[plain]})

    def test_scene_labels_come_from_the_deepest_rule_with_an_active_label(self):
        cat = self.seed_catalog()  # oven：dish -> 标签 steak
        store = self.store()
        oven = store.get_scene_by_code("oven")["id"]
        scene = store.create_scene_label(oven, {"code": "beef", "name": "牛肉"})
        beef = next(l["id"] for l in scene["labels"] if l["code"] == "beef")
        store.create_scene_rule(oven, {"category_id": cat["steak"], "label_id": beef})

        def mapped(*ids):
            return {cid: label["code"] for cid, label in store.map_categories_to_scene_labels(oven, list(ids)).items()}

        self.assertEqual(mapped(cat["steak"], cat["dish"]), {cat["steak"]: "beef", cat["dish"]: "steak"})
        store.update_scene_label(oven, beef, {"is_active": False})  # 退回到更宽泛的规则
        self.assertEqual(mapped(cat["steak"]), {cat["steak"]: "steak"})
        steak_label = self.sql("SELECT id FROM scene_labels WHERE code = 'steak'").scalar_one()
        store.update_scene_label(oven, steak_label, {"is_active": False})
        self.assertEqual(mapped(cat["steak"], cat["dish"]), {})
        self.assertEqual(mapped(), {})

    def test_sync_rewrites_payloads_and_the_ledger(self):
        import uuid as uuid_mod

        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        cat = self.seed_catalog()
        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_sync', 'current') RETURNING id"
        ).scalar_one()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto", food_state="cooked")
        plain = self.insert(store, 2)
        orphan = self.insert(store, 3)  # 在 PG 里，从来没有写进这个 collection

        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_sync", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        for image, unit in ((steak, [1, 0, 0, 0]), (plain, [0, 1, 0, 0])):
            vectors.upsert(image, unit, {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "x"})
        store.upsert_image_embedding(model_id=model_id, image_id=steak, synced_version=1)  # plain：台账写入"失败"了
        self.sql("UPDATE image_embeddings SET embedded_at = now() - interval '1 day'")

        model = ActiveModel(id=model_id, name="m", version="1", collection="c_sync", dim=4, distance="cosine")
        uc = SyncSceneMembershipsUseCase(store, vectors, model=model)
        totals = uc.run_all(limit=2)
        self.assertEqual(
            {k: totals[k] for k in ("processed", "payload_pushed", "payload_missing", "batches")},
            {"processed": 3, "payload_pushed": 2, "payload_missing": [orphan], "batches": 2},
        )
        points = {p["id"]: p["payload"] for p in vectors.search([1, 0, 0, 0], top_k=10)}
        self.assertEqual(points[steak], {"category_id": cat["steak"], "scenes": ["general", "oven"], "food_state": "cooked", "label_status": "auto"})
        self.assertEqual(points[plain]["scenes"], ["general"])
        self.assertEqual(len(vectors.search([1, 0, 0, 0], top_k=10, scene="oven")), 1)  # 过滤看到了新的 payload
        ledger = {r[0]: (r[1], r[2]) for r in self.sql(
            "SELECT image_id::text, synced_version, embedded_at < now() - interval '1 hour' FROM image_embeddings"
        ).fetchall()}
        self.assertEqual(ledger, {steak: (1, True), plain: (1, False)})  # plain 的行是新的；steak 的 embedded_at 保留

        # 规则修改通过下一次同步作用到向量过滤
        oven = store.get_scene_by_code("oven")["id"]
        rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()
        store.delete_scene_rule(oven, rule)
        first = uc.run_batch(limit=10)
        self.assertEqual((first["membership_changed"], first["done"], first["next_after"]), (1, True, None))
        self.assertEqual(vectors.search([1, 0, 0, 0], top_k=10, scene="oven"), [])
        self.assertEqual(self.sql("SELECT synced_version FROM image_embeddings WHERE image_id = :i", {"i": uuid_mod.UUID(steak)}).scalar_one(), 2)

    def test_a_manual_membership_reaches_the_vector_filter_at_once(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1 import scene_memberships as routes
        from app.bootstrap.state import ApplicationState
        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.core.exceptions import register_exception_handlers
        from app.core.middleware import TraceIdMiddleware
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.manage_scenes import ManageScenesUseCase
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_manual', 'current') RETURNING id"
        ).scalar_one()
        plain = self.insert(store, 1)
        orphan = self.insert(store, 2)  # 在 PG 里，从来没有写进这个 collection
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_manual", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        vectors.upsert(plain, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "unlabeled"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_manual", dim=4, distance="cosine")

        runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(scenes_uc=ManageScenesUseCase(store)),
            retrieval=SimpleNamespace(scene_sync_uc=SyncSceneMembershipsUseCase(store, vectors, model=model)),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(routes, "get_application_state", return_value=runtime):
            oven = store.get_scene_by_code("oven")["id"]
            path = f"/api/images/{plain}/scene-memberships/{oven}"

            def oven_hits():
                return [hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")]

            def synced(image):
                return self.sql(
                    "SELECT e.synced_version = i.payload_version FROM images i "
                    "JOIN image_embeddings e ON e.image_id = i.id WHERE i.id = :i", {"i": uuid.UUID(image)}
                ).scalar_one()

            # 手工包含：向量过滤在同一个请求里就能看到，台账也一致
            response = client.put(path, json={"is_member": True})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("pushed", False, ["general", "oven"]))
            self.assertEqual(oven_hits(), [plain])
            self.assertTrue(synced(plain))

            # 回到按规则（没有匹配的）：又从过滤里出去了
            body = client.delete(path).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("pushed", False, ["general"]))
            self.assertEqual(oven_hits(), [])

            # 当前 collection 里没有向量：存在 PG 里，搜索看不到
            body = client.put(f"/api/images/{orphan}/scene-memberships/{oven}", json={"is_member": True}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"]), ("missing", True))
            self.assertEqual(store.get_effective_scene_codes(orphan), ["general", "oven"])

            # Qdrant 失败：仍然保存，告诉客户端需要同步
            with patch.object(vectors, "set_payloads", side_effect=RuntimeError("qdrant down")):
                body = client.put(path, json={"is_member": True}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("failed", True, ["general", "oven"]))
            self.assertEqual(oven_hits(), [])
            self.assertFalse(synced(plain))  # 台账如实记录：payload 落后了

            # 检索服务没有构建：只靠 PostgreSQL 也能接受修改
            runtime.retrieval, runtime.retrieval_error = None, "embedding model unavailable"
            body = client.put(path, json={"is_member": False}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("not_ready", True, ["general"]))

    def test_a_payload_superseded_while_it_was_written_is_pushed_again(self):
        from unittest.mock import patch

        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_race', 'current') RETURNING id"
        ).scalar_one()
        image = self.insert(store, 1)
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_race", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        vectors.upsert(image, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "unlabeled"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_race", dim=4, distance="cosine")
        uc = SyncSceneMembershipsUseCase(store, vectors, model=model)
        oven = store.get_scene_by_code("oven")["id"]

        # 另一个写入方在我们读和写之间把图片加入了 oven（并推送了）：
        # 我们的 payload 是较旧的那个，所以不能留在 collection 里
        real_set = vectors.set_payloads
        calls = []

        def racing_set(payloads):
            calls.append({k: v["scenes"] for k, v in payloads.items()})
            if len(calls) == 1:
                store.put_manual_scene_membership(image, oven, is_member=True)
                uc.push_images([image])  # 另一个写入方自己的推送先到
            return real_set(payloads)

        with patch.object(vectors, "set_payloads", side_effect=racing_set):
            result = uc.push_images([image])
        self.assertEqual(calls[0], {image: ["general"]})  # 过时的写入……
        self.assertEqual(calls[-1], {image: ["general", "oven"]})  # ……之后跟着当前的写入
        self.assertEqual((list(result.pushed), result.missing, result.unconfirmed), ([image], [], []))
        self.assertEqual([hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")], [image])

    def run_while_locked(self, action, *, shared):
        """在这个测试持有目录锁期间，在一个线程里启动 action；它等待了就返回 True。"""
        import threading

        from sqlalchemy import text

        from app.repositories.catalog_lock import CATALOG_LOCK_KEY

        done = threading.Event()
        failure = []

        def run():
            try:
                action()
            except Exception as exc:  # 由下面的断言暴露出来
                failure.append(exc)
            done.set()

        lock = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        with self.engine.connect() as holder:
            tx = holder.begin()
            holder.execute(text(f"SELECT {lock}(:key)"), {"key": CATALOG_LOCK_KEY})
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            waited = not done.wait(0.6)
            tx.commit()
            self.assertTrue(done.wait(10), "the action never finished after the lock was released")
            worker.join(10)
        self.assertEqual(failure, [])
        return waited

    def test_catalog_writes_and_recomputes_wait_for_the_catalog_lock(self):
        store = self.store()
        image = self.insert(store, 1)
        writes = {
            "category write": lambda: store.create_category({"code": "dish", "names": [self.zh("菜品")]}),
            "scene write": lambda: store.create_scene({"code": "steam_oven", "name": "蒸烤箱"}),
            "membership recompute": lambda: store.recompute_image_scenes(image, bump_version=False),
            "batch recompute": lambda: store.recompute_scene_memberships(limit=10),
        }
        for name, action in writes.items():
            with self.subTest(name):
                self.assertTrue(self.run_while_locked(action, shared=False), f"{name} did not wait for an admin write")
        # 重算只读取目录：它们和其他读取方共享锁
        self.assertFalse(self.run_while_locked(lambda: store.recompute_image_scenes(image, bump_version=False), shared=True))
        # ……而管理写入要等它们
        self.assertTrue(self.run_while_locked(lambda: store.create_scene({"code": "air_fryer", "name": "空气炸锅"}), shared=True))
        self.assertEqual([n["code"] for n in store.list_categories()], ["dish"])


if __name__ == "__main__":
    unittest.main()
