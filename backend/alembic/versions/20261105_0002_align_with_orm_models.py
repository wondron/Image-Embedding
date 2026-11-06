"""Align PG v0.1 schema with app.models (ORM is now the schema SSOT).

Revision ID: 20261105_0002
Revises: 20261104_0001
Create Date: 2026-11-05

What changes (0001 -> app.models):
- Constraint / index names follow app.db.base.NAMING_CONVENTION (pk_/fk_/uq_/ck_/ix_).
- Enum columns text -> varchar(32); CHECK lists unchanged.
- New invariants:
    images:              width/height NOT NULL and > 0; sha256 is 32 bytes;
                         (label_status = 'unlabeled') = (category_id IS NULL)
    categories:          not own parent
    scene_rules:         one rule per (scene, category) and (scene, cuisine);
                         label_id needs category_id; label must belong to the same scene
                         (composite FK -> scene_labels(scene_id, id))
    image_scenes:        only manual rows may exclude (is_member = false)
    embedding_models:    dim > 0, input_size > 0
    external_candidates: reviewed_at set iff not pending; image_id only when accepted
- ON DELETE: scene children CASCADE; categories.heating_group_id and
  external_candidates.image_id SET NULL; images/categories -> categories RESTRICT;
  image_embeddings.model_id CASCADE.
- Defaults: images.label_status 'unlabeled', image_scenes.is_member true,
  embedding_models.status 'building', external_candidates.id gen_random_uuid().
- Indexes: drop ones covered by a PK/unique prefix; add cuisines GIN, license,
  image_embeddings.image_id, pending-candidates partial, scene-member partial.
- Data: include_all scenes (general) hold every image implicitly, so their
  image_scenes rows are removed. Images that had a manual exclusion from such a
  scene get payload_version + 1 (their Qdrant payload must now list the scene).
- Table/column comments from the ORM.

Existing rows that violate a new constraint make the upgrade fail with that
constraint's name; the whole revision runs in one transaction.
"""
from __future__ import annotations

import re
from typing import Sequence, Union

from alembic import op

revision: str = "20261105_0002"
down_revision: Union[str, None] = "20261104_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
-- 1. Primary keys -> pk_<table>
ALTER TABLE heating_groups      RENAME CONSTRAINT heating_groups_pkey      TO pk_heating_groups;
ALTER TABLE categories          RENAME CONSTRAINT categories_pkey          TO pk_categories;
ALTER TABLE images              RENAME CONSTRAINT images_pkey              TO pk_images;
ALTER TABLE image_deletions     RENAME CONSTRAINT image_deletions_pkey     TO pk_image_deletions;
ALTER TABLE scenes              RENAME CONSTRAINT scenes_pkey              TO pk_scenes;
ALTER TABLE scene_labels        RENAME CONSTRAINT scene_labels_pkey        TO pk_scene_labels;
ALTER TABLE scene_rules         RENAME CONSTRAINT scene_rules_pkey         TO pk_scene_rules;
ALTER TABLE embedding_models    RENAME CONSTRAINT embedding_models_pkey    TO pk_embedding_models;
ALTER TABLE external_candidates RENAME CONSTRAINT external_candidates_pkey TO pk_external_candidates;

-- 2. Indexes: drop redundant / replaced ones before retyping columns
DROP INDEX idx_categories_is_active;
DROP INDEX idx_category_names_category_id;   -- PK (category_id, lang, name) covers it
DROP INDEX idx_images_created_at;            -- recreated ascending below
DROP INDEX idx_scene_labels_scene_id;        -- uq (scene_id, code) covers it
DROP INDEX idx_scene_rules_scene_id;         -- uq (scene_id, category_id) covers it
DROP INDEX idx_scene_rules_cuisine;
DROP INDEX idx_external_candidates_review_created;

-- 3. Enum columns: text -> varchar(32); CHECK constraints keep their names
ALTER TABLE category_names ALTER COLUMN kind TYPE varchar(32);
ALTER TABLE images
  ALTER COLUMN source_type  TYPE varchar(32),
  ALTER COLUMN license      TYPE varchar(32),
  ALTER COLUMN food_state   TYPE varchar(32),
  ALTER COLUMN label_status TYPE varchar(32);
ALTER TABLE image_scenes ALTER COLUMN origin TYPE varchar(32);
ALTER TABLE embedding_models
  ALTER COLUMN distance TYPE varchar(32),
  ALTER COLUMN status   TYPE varchar(32);
ALTER TABLE external_candidates ALTER COLUMN review_status TYPE varchar(32);

-- 4. NOT NULL / defaults
ALTER TABLE images
  ALTER COLUMN width SET NOT NULL,
  ALTER COLUMN height SET NOT NULL,
  ALTER COLUMN label_status SET DEFAULT 'unlabeled';
ALTER TABLE image_scenes ALTER COLUMN is_member SET DEFAULT true;
ALTER TABLE embedding_models ALTER COLUMN status SET DEFAULT 'building';
ALTER TABLE external_candidates ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- 5. Unique constraints
ALTER TABLE scene_labels RENAME CONSTRAINT uq_scene_labels_scene_code TO uq_scene_labels_scene_id_code;
ALTER TABLE scene_labels ADD CONSTRAINT uq_scene_labels_scene_id_id UNIQUE (scene_id, id);
ALTER TABLE scene_rules
  ADD CONSTRAINT uq_scene_rules_scene_id_category_id UNIQUE (scene_id, category_id),
  ADD CONSTRAINT uq_scene_rules_scene_id_cuisine UNIQUE (scene_id, cuisine);
ALTER TABLE external_candidates
  RENAME CONSTRAINT uq_external_candidates_provider_url TO uq_external_candidates_provider_source_url;

-- 6. Foreign keys: convention names + ON DELETE behaviour
ALTER TABLE categories
  DROP CONSTRAINT categories_parent_id_fkey,
  ADD CONSTRAINT fk_categories_parent_id_categories
    FOREIGN KEY (parent_id) REFERENCES categories (id) ON DELETE RESTRICT,
  DROP CONSTRAINT categories_heating_group_id_fkey,
  ADD CONSTRAINT fk_categories_heating_group_id_heating_groups
    FOREIGN KEY (heating_group_id) REFERENCES heating_groups (id) ON DELETE SET NULL;
ALTER TABLE category_names
  RENAME CONSTRAINT category_names_category_id_fkey TO fk_category_names_category_id_categories;
ALTER TABLE images
  DROP CONSTRAINT images_category_id_fkey,
  ADD CONSTRAINT fk_images_category_id_categories
    FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE RESTRICT;
ALTER TABLE scene_labels
  DROP CONSTRAINT scene_labels_scene_id_fkey,
  ADD CONSTRAINT fk_scene_labels_scene_id_scenes
    FOREIGN KEY (scene_id) REFERENCES scenes (id) ON DELETE CASCADE;
ALTER TABLE scene_rules
  DROP CONSTRAINT scene_rules_scene_id_fkey,
  ADD CONSTRAINT fk_scene_rules_scene_id_scenes
    FOREIGN KEY (scene_id) REFERENCES scenes (id) ON DELETE CASCADE,
  DROP CONSTRAINT scene_rules_category_id_fkey,
  ADD CONSTRAINT fk_scene_rules_category_id_categories
    FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE,
  DROP CONSTRAINT scene_rules_label_id_fkey,
  ADD CONSTRAINT fk_scene_rules_scene_id_label_id_scene_labels
    FOREIGN KEY (scene_id, label_id) REFERENCES scene_labels (scene_id, id);
ALTER TABLE image_scenes
  RENAME CONSTRAINT image_scenes_image_id_fkey TO fk_image_scenes_image_id_images;
ALTER TABLE image_scenes
  DROP CONSTRAINT image_scenes_scene_id_fkey,
  ADD CONSTRAINT fk_image_scenes_scene_id_scenes
    FOREIGN KEY (scene_id) REFERENCES scenes (id) ON DELETE CASCADE;
ALTER TABLE image_embeddings
  RENAME CONSTRAINT image_embeddings_image_id_fkey TO fk_image_embeddings_image_id_images;
ALTER TABLE image_embeddings
  DROP CONSTRAINT image_embeddings_model_id_fkey,
  ADD CONSTRAINT fk_image_embeddings_model_id_embedding_models
    FOREIGN KEY (model_id) REFERENCES embedding_models (id) ON DELETE CASCADE;
ALTER TABLE external_candidates
  DROP CONSTRAINT external_candidates_image_id_fkey,
  ADD CONSTRAINT fk_external_candidates_image_id_images
    FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE SET NULL;

-- 7. CHECK constraints
ALTER TABLE categories
  ADD CONSTRAINT ck_categories_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id);
ALTER TABLE images
  ADD CONSTRAINT ck_images_sha256_len CHECK (octet_length(sha256) = 32),
  ADD CONSTRAINT ck_images_size_positive CHECK (width > 0 AND height > 0),
  ADD CONSTRAINT ck_images_label_matches_category
    CHECK ((label_status = 'unlabeled') = (category_id IS NULL));
ALTER TABLE scene_rules
  DROP CONSTRAINT ck_scene_rules_xor,
  ADD CONSTRAINT ck_scene_rules_one_target CHECK ((category_id IS NULL) <> (cuisine IS NULL)),
  ADD CONSTRAINT ck_scene_rules_label_needs_category
    CHECK (label_id IS NULL OR category_id IS NOT NULL);
ALTER TABLE image_scenes
  ADD CONSTRAINT ck_image_scenes_exclude_only_manual CHECK (is_member OR origin = 'manual');
ALTER TABLE embedding_models
  ADD CONSTRAINT ck_embedding_models_dim_positive CHECK (dim > 0),
  ADD CONSTRAINT ck_embedding_models_input_size_positive CHECK (input_size > 0);
ALTER TABLE external_candidates
  ADD CONSTRAINT ck_external_candidates_reviewed_at_matches
    CHECK ((review_status = 'pending') = (reviewed_at IS NULL)),
  ADD CONSTRAINT ck_external_candidates_image_only_when_accepted
    CHECK (image_id IS NULL OR review_status = 'accepted');

-- 8. Indexes: rename to ix_* and add new ones
ALTER INDEX idx_categories_parent_id        RENAME TO ix_categories_parent_id;
ALTER INDEX idx_categories_heating_group_id RENAME TO ix_categories_heating_group_id;
ALTER INDEX idx_categories_path_gist        RENAME TO ix_categories_path_gist;
CREATE INDEX ix_categories_cuisines_gin ON categories USING gin (cuisines);

ALTER INDEX uq_category_names_primary    RENAME TO uq_category_names_one_primary;
ALTER INDEX idx_category_names_lang_name RENAME TO ix_category_names_lang_name;

ALTER INDEX idx_images_category_id  RENAME TO ix_images_category_id;
ALTER INDEX idx_images_label_status RENAME TO ix_images_label_status;
ALTER INDEX idx_images_source_type  RENAME TO ix_images_source_type;
CREATE INDEX ix_images_license    ON images (license);
CREATE INDEX ix_images_created_at ON images (created_at);

ALTER INDEX idx_image_deletions_deleted_at RENAME TO ix_image_deletions_deleted_at;

ALTER INDEX idx_scene_rules_category_id RENAME TO ix_scene_rules_category_id;

CREATE INDEX ix_image_scenes_scene_member ON image_scenes (scene_id) WHERE is_member;

ALTER INDEX uq_embedding_models_current RENAME TO uq_embedding_models_one_current;

CREATE INDEX ix_image_embeddings_image_id ON image_embeddings (image_id);

CREATE INDEX ix_external_candidates_image_id ON external_candidates (image_id);
CREATE INDEX ix_external_candidates_pending
  ON external_candidates (created_at) WHERE review_status = 'pending';

-- 9. Data: include_all scenes hold every image implicitly (no image_scenes rows)
UPDATE images SET payload_version = payload_version + 1, updated_at = now()
WHERE id IN (
  SELECT isc.image_id FROM image_scenes isc
  JOIN scenes s ON s.id = isc.scene_id
  WHERE s.include_all AND NOT isc.is_member
);
DELETE FROM image_scenes isc USING scenes s
WHERE s.id = isc.scene_id AND s.include_all;

-- 10. Comments (generated once from app.models)
COMMENT ON TABLE heating_groups IS '加热组';
COMMENT ON COLUMN heating_groups.code IS '加热组编码';
COMMENT ON COLUMN heating_groups.name IS '名称';
COMMENT ON COLUMN heating_groups.description IS '说明';

COMMENT ON TABLE categories IS '类别树';
COMMENT ON COLUMN categories.code IS '稳定编码，如 dish.pizza；配置和规则里用它引用类别';
COMMENT ON COLUMN categories.parent_id IS '父节点，根节点为空';
COMMENT ON COLUMN categories.path IS 'ID 链，如 2.15.137；插入或移动节点时自动重算整棵子树，不手动写';
COMMENT ON COLUMN categories.cuisines IS '菜系，如 {american,italian}；子孙沿用祖先的菜系';
COMMENT ON COLUMN categories.heating_group_id IS '加热组；可设在任意节点，子孙没设时沿用最近祖先的';
COMMENT ON COLUMN categories.sort_order IS '同级排序';
COMMENT ON COLUMN categories.is_active IS '停用后不再用于标注';
COMMENT ON COLUMN categories.created_at IS '创建时间';
COMMENT ON COLUMN categories.updated_at IS '更新时间';

COMMENT ON TABLE category_names IS '类别名称（多语言）';
COMMENT ON COLUMN category_names.category_id IS '所属类别';
COMMENT ON COLUMN category_names.lang IS '语言代码：zh、en、fr、ja……';
COMMENT ON COLUMN category_names.name IS '名称';
COMMENT ON COLUMN category_names.kind IS 'primary 主名 / alias 别名';

COMMENT ON TABLE images IS '图片';
COMMENT ON COLUMN images.id IS '原图 SHA-256 的前 16 字节；同时是 S3 文件名和 Qdrant 点 ID';
COMMENT ON COLUMN images.sha256 IS '原图 SHA-256，用来校验和去重';
COMMENT ON COLUMN images.original_url IS '原图的完整 https 地址';
COMMENT ON COLUMN images.thumb_url IS '缩略图的完整 https 地址（长边 512，WebP）';
COMMENT ON COLUMN images.width IS '原图宽（像素）';
COMMENT ON COLUMN images.height IS '原图高（像素）';
COMMENT ON COLUMN images.source_type IS '来源大类';
COMMENT ON COLUMN images.license IS '许可';
COMMENT ON COLUMN images.category_id IS '类别，可以标到任意层级；未标注时为空';
COMMENT ON COLUMN images.food_state IS '食物状态：生 / 熟 / 冷冻';
COMMENT ON COLUMN images.label_status IS '标注状态';
COMMENT ON COLUMN images.payload_version IS '类别、状态或所属场景变化时加 1，用来判断 Qdrant 里的 payload 要不要重推';
COMMENT ON COLUMN images.created_at IS '创建时间';
COMMENT ON COLUMN images.updated_at IS '更新时间';

COMMENT ON TABLE image_deletions IS '图片清理队列';
COMMENT ON COLUMN image_deletions.image_id IS '被删除的图片；图片行已删除，所以不设外键';
COMMENT ON COLUMN image_deletions.original_url IS '要清理的原图';
COMMENT ON COLUMN image_deletions.thumb_url IS '要清理的缩略图';
COMMENT ON COLUMN image_deletions.deleted_at IS '删除时间';

COMMENT ON TABLE scenes IS '应用场景';
COMMENT ON COLUMN scenes.code IS '场景编码，接口传入、写进 Qdrant payload，如 general、oven';
COMMENT ON COLUMN scenes.name IS '名称，如 通用、一体机';
COMMENT ON COLUMN scenes.description IS '说明';
COMMENT ON COLUMN scenes.include_all IS '是否包含全部图片；通用场景为是';
COMMENT ON COLUMN scenes.outputs_heating IS '结果是否另带加热组和食物状态；一体机场景为是';
COMMENT ON COLUMN scenes.is_active IS '停用后不再分配新图片';
COMMENT ON COLUMN scenes.created_at IS '创建时间';

COMMENT ON TABLE scene_labels IS '场景类别';
COMMENT ON COLUMN scene_labels.scene_id IS '所属场景';
COMMENT ON COLUMN scene_labels.code IS '场景类别编码，场景内唯一，如 steak';
COMMENT ON COLUMN scene_labels.name IS '名称，如 牛排';
COMMENT ON COLUMN scene_labels.sort_order IS '排序';
COMMENT ON COLUMN scene_labels.is_active IS '停用后不再输出';

COMMENT ON TABLE scene_rules IS '场景分配规则';
COMMENT ON COLUMN scene_rules.scene_id IS '所属场景';
COMMENT ON COLUMN scene_rules.category_id IS '按类别：这个类别及其所有子孙';
COMMENT ON COLUMN scene_rules.cuisine IS '按菜系：菜系包含它的类别';
COMMENT ON COLUMN scene_rules.label_id IS '映射到本场景自己的类别（scene_labels），可为空';

COMMENT ON TABLE image_scenes IS '图片与场景的关系';
COMMENT ON COLUMN image_scenes.origin IS 'rule 按规则分配 / manual 人工调整';
COMMENT ON COLUMN image_scenes.is_member IS '是否属于该场景；人工排除时为否';
COMMENT ON COLUMN image_scenes.updated_at IS '更新时间';

COMMENT ON TABLE embedding_models IS '模型注册表';
COMMENT ON COLUMN embedding_models.name IS '模型名，如 dinov3_vith16plus_lora';
COMMENT ON COLUMN embedding_models.version IS '版本；name 加 version 唯一';
COMMENT ON COLUMN embedding_models.dim IS '向量维度，如 1024';
COMMENT ON COLUMN embedding_models.distance IS '距离';
COMMENT ON COLUMN embedding_models.input_size IS '输入尺寸，如 336';
COMMENT ON COLUMN embedding_models.preprocess IS '预处理参数：均值方差、缩放方式等';
COMMENT ON COLUMN embedding_models.weights_uri IS '权重位置，含 LoRA';
COMMENT ON COLUMN embedding_models.collection IS '对应的 Qdrant collection 名';
COMMENT ON COLUMN embedding_models.status IS 'building / ready / current / retired';
COMMENT ON COLUMN embedding_models.activated_at IS '切换为当前的时间';
COMMENT ON COLUMN embedding_models.created_at IS '创建时间';

COMMENT ON TABLE image_embeddings IS '向量写入记录';
COMMENT ON COLUMN image_embeddings.synced_version IS '写入 Qdrant 时这张图的 payload_version；和图片当前的不一致就要重推 payload';
COMMENT ON COLUMN image_embeddings.embedded_at IS '写入时间';

COMMENT ON TABLE external_candidates IS '外部图搜候选';
COMMENT ON COLUMN external_candidates.provider IS '外部图搜服务名';
COMMENT ON COLUMN external_candidates.source_url IS '结果原链接；同一服务的同一链接只存一次';
COMMENT ON COLUMN external_candidates.staged_url IS '暂存文件的完整 https 地址（S3 staging/）';
COMMENT ON COLUMN external_candidates.external_label IS '外部返回的标签文字，审核时参考';
COMMENT ON COLUMN external_candidates.query_ref IS '触发这次搜索的查询，如请求 ID';
COMMENT ON COLUMN external_candidates.review_status IS 'pending / accepted / rejected';
COMMENT ON COLUMN external_candidates.reviewed_at IS '审核时间，待审核时为空';
COMMENT ON COLUMN external_candidates.image_id IS '入库后的图片 ID，未入库时为空';
COMMENT ON COLUMN external_candidates.created_at IS '创建时间';
"""


_COMMENT_RE = re.compile(r"^COMMENT ON (TABLE|COLUMN) (\S+) IS ", re.MULTILINE)

# 10. Comments: 0001 had none -> clear exactly what the upgrade set
CLEAR_COMMENTS_SQL = "\n".join(
    f"COMMENT ON {kind} {target} IS NULL;" for kind, target in _COMMENT_RE.findall(UPGRADE_SQL)
)

DOWNGRADE_SQL = r"""
-- 9. Data: re-materialize include_all rows (0001 semantics)
INSERT INTO image_scenes (image_id, scene_id, origin, is_member, updated_at)
SELECT i.id, s.id, 'rule', true, now()
FROM images i CROSS JOIN scenes s
WHERE s.include_all AND s.is_active
ON CONFLICT (image_id, scene_id) DO NOTHING;

-- 8. Indexes
DROP INDEX ix_external_candidates_pending;
DROP INDEX ix_external_candidates_image_id;
DROP INDEX ix_image_embeddings_image_id;
ALTER INDEX uq_embedding_models_one_current RENAME TO uq_embedding_models_current;
DROP INDEX ix_image_scenes_scene_member;
ALTER INDEX ix_scene_rules_category_id RENAME TO idx_scene_rules_category_id;
ALTER INDEX ix_image_deletions_deleted_at RENAME TO idx_image_deletions_deleted_at;
DROP INDEX ix_images_created_at;
DROP INDEX ix_images_license;
ALTER INDEX ix_images_source_type  RENAME TO idx_images_source_type;
ALTER INDEX ix_images_label_status RENAME TO idx_images_label_status;
ALTER INDEX ix_images_category_id  RENAME TO idx_images_category_id;
ALTER INDEX ix_category_names_lang_name   RENAME TO idx_category_names_lang_name;
ALTER INDEX uq_category_names_one_primary RENAME TO uq_category_names_primary;
DROP INDEX ix_categories_cuisines_gin;
ALTER INDEX ix_categories_path_gist        RENAME TO idx_categories_path_gist;
ALTER INDEX ix_categories_heating_group_id RENAME TO idx_categories_heating_group_id;
ALTER INDEX ix_categories_parent_id        RENAME TO idx_categories_parent_id;

-- 7. CHECK constraints
ALTER TABLE external_candidates
  DROP CONSTRAINT ck_external_candidates_image_only_when_accepted,
  DROP CONSTRAINT ck_external_candidates_reviewed_at_matches;
ALTER TABLE embedding_models
  DROP CONSTRAINT ck_embedding_models_input_size_positive,
  DROP CONSTRAINT ck_embedding_models_dim_positive;
ALTER TABLE image_scenes DROP CONSTRAINT ck_image_scenes_exclude_only_manual;
ALTER TABLE scene_rules
  DROP CONSTRAINT ck_scene_rules_label_needs_category,
  DROP CONSTRAINT ck_scene_rules_one_target,
  ADD CONSTRAINT ck_scene_rules_xor CHECK (
    (category_id IS NOT NULL AND cuisine IS NULL)
    OR (category_id IS NULL AND cuisine IS NOT NULL)
  );
ALTER TABLE images
  DROP CONSTRAINT ck_images_label_matches_category,
  DROP CONSTRAINT ck_images_size_positive,
  DROP CONSTRAINT ck_images_sha256_len;
ALTER TABLE categories DROP CONSTRAINT ck_categories_not_own_parent;

-- 6. Foreign keys back to 0001 (unnamed -> PG default names, NO ACTION)
ALTER TABLE external_candidates
  DROP CONSTRAINT fk_external_candidates_image_id_images,
  ADD CONSTRAINT external_candidates_image_id_fkey FOREIGN KEY (image_id) REFERENCES images (id);
ALTER TABLE image_embeddings
  DROP CONSTRAINT fk_image_embeddings_model_id_embedding_models,
  ADD CONSTRAINT image_embeddings_model_id_fkey FOREIGN KEY (model_id) REFERENCES embedding_models (id);
ALTER TABLE image_embeddings
  RENAME CONSTRAINT fk_image_embeddings_image_id_images TO image_embeddings_image_id_fkey;
ALTER TABLE image_scenes
  DROP CONSTRAINT fk_image_scenes_scene_id_scenes,
  ADD CONSTRAINT image_scenes_scene_id_fkey FOREIGN KEY (scene_id) REFERENCES scenes (id);
ALTER TABLE image_scenes
  RENAME CONSTRAINT fk_image_scenes_image_id_images TO image_scenes_image_id_fkey;
ALTER TABLE scene_rules
  DROP CONSTRAINT fk_scene_rules_scene_id_label_id_scene_labels,
  ADD CONSTRAINT scene_rules_label_id_fkey FOREIGN KEY (label_id) REFERENCES scene_labels (id),
  DROP CONSTRAINT fk_scene_rules_category_id_categories,
  ADD CONSTRAINT scene_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories (id),
  DROP CONSTRAINT fk_scene_rules_scene_id_scenes,
  ADD CONSTRAINT scene_rules_scene_id_fkey FOREIGN KEY (scene_id) REFERENCES scenes (id);
ALTER TABLE scene_labels
  DROP CONSTRAINT fk_scene_labels_scene_id_scenes,
  ADD CONSTRAINT scene_labels_scene_id_fkey FOREIGN KEY (scene_id) REFERENCES scenes (id);
ALTER TABLE images
  DROP CONSTRAINT fk_images_category_id_categories,
  ADD CONSTRAINT images_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories (id);
ALTER TABLE category_names
  RENAME CONSTRAINT fk_category_names_category_id_categories TO category_names_category_id_fkey;
ALTER TABLE categories
  DROP CONSTRAINT fk_categories_heating_group_id_heating_groups,
  ADD CONSTRAINT categories_heating_group_id_fkey
    FOREIGN KEY (heating_group_id) REFERENCES heating_groups (id),
  DROP CONSTRAINT fk_categories_parent_id_categories,
  ADD CONSTRAINT categories_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES categories (id);

-- 5. Unique constraints
ALTER TABLE external_candidates
  RENAME CONSTRAINT uq_external_candidates_provider_source_url TO uq_external_candidates_provider_url;
ALTER TABLE scene_rules
  DROP CONSTRAINT uq_scene_rules_scene_id_cuisine,
  DROP CONSTRAINT uq_scene_rules_scene_id_category_id;
ALTER TABLE scene_labels DROP CONSTRAINT uq_scene_labels_scene_id_id;
ALTER TABLE scene_labels RENAME CONSTRAINT uq_scene_labels_scene_id_code TO uq_scene_labels_scene_code;

-- 4. NOT NULL / defaults
ALTER TABLE external_candidates ALTER COLUMN id DROP DEFAULT;
ALTER TABLE embedding_models ALTER COLUMN status DROP DEFAULT;
ALTER TABLE image_scenes ALTER COLUMN is_member DROP DEFAULT;
ALTER TABLE images
  ALTER COLUMN label_status DROP DEFAULT,
  ALTER COLUMN height DROP NOT NULL,
  ALTER COLUMN width DROP NOT NULL;

-- 3. Enum columns back to text
ALTER TABLE external_candidates ALTER COLUMN review_status TYPE text;
ALTER TABLE embedding_models
  ALTER COLUMN status   TYPE text,
  ALTER COLUMN distance TYPE text;
ALTER TABLE image_scenes ALTER COLUMN origin TYPE text;
ALTER TABLE images
  ALTER COLUMN label_status TYPE text,
  ALTER COLUMN food_state   TYPE text,
  ALTER COLUMN license      TYPE text,
  ALTER COLUMN source_type  TYPE text;
ALTER TABLE category_names ALTER COLUMN kind TYPE text;

-- 2. Re-create dropped 0001 indexes
CREATE INDEX idx_external_candidates_review_created ON external_candidates (review_status, created_at);
CREATE INDEX idx_scene_rules_cuisine ON scene_rules (cuisine);
CREATE INDEX idx_scene_rules_scene_id ON scene_rules (scene_id);
CREATE INDEX idx_scene_labels_scene_id ON scene_labels (scene_id);
CREATE INDEX idx_images_created_at ON images (created_at DESC);
CREATE INDEX idx_category_names_category_id ON category_names (category_id);
CREATE INDEX idx_categories_is_active ON categories (is_active);

-- 1. Primary keys back to PG default names
ALTER TABLE external_candidates RENAME CONSTRAINT pk_external_candidates TO external_candidates_pkey;
ALTER TABLE embedding_models    RENAME CONSTRAINT pk_embedding_models    TO embedding_models_pkey;
ALTER TABLE scene_rules         RENAME CONSTRAINT pk_scene_rules         TO scene_rules_pkey;
ALTER TABLE scene_labels        RENAME CONSTRAINT pk_scene_labels        TO scene_labels_pkey;
ALTER TABLE scenes              RENAME CONSTRAINT pk_scenes              TO scenes_pkey;
ALTER TABLE image_deletions     RENAME CONSTRAINT pk_image_deletions     TO image_deletions_pkey;
ALTER TABLE images              RENAME CONSTRAINT pk_images              TO images_pkey;
ALTER TABLE categories          RENAME CONSTRAINT pk_categories          TO categories_pkey;
ALTER TABLE heating_groups      RENAME CONSTRAINT pk_heating_groups      TO heating_groups_pkey;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(CLEAR_COMMENTS_SQL)
    op.execute(DOWNGRADE_SQL)
