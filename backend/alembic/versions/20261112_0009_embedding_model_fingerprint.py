"""embedding_models.fingerprint: which encoder wrote a model version's collection.

Revision ID: 20261112_0009
Revises: 20261111_0008
Create Date: 2026-11-12

{spec, probe}: the encoder's preprocessing (and where its weights are) plus its vector for one
fixed test image (app.clients.inference.Embedder.fingerprint). Retrieval compares it with the
encoder it is about to use, so a changed EMBEDDING_CONFIG can no longer query or write a
collection another model made; /api/embedding-models registers, fills and switches versions by it.

A nullable column without a default: metadata only, no table rewrite, no lock held for long.
Rows registered before it stay NULL until the API records the fingerprint of the encoder that
starts on them (after checking a few stored images against their vectors).

Downgrade drops the column: the recorded fingerprints are lost (the API records them again).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261112_0009"
down_revision: Union[str, None] = "20261111_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE embedding_models ADD COLUMN fingerprint jsonb;
COMMENT ON COLUMN embedding_models.fingerprint IS '编码器指纹：预处理参数和固定测试图的输出向量，用来认出写这个 collection 的编码器；为空表示还没记录';
"""

DOWNGRADE_SQL = r"""
ALTER TABLE embedding_models DROP COLUMN fingerprint;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
