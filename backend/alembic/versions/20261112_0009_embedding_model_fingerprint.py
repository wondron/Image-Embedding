"""embedding_models.fingerprint：哪个编码器写入了一个模型版本的 collection。

Revision ID: 20261112_0009
Revises: 20261111_0008
Create Date: 2026-11-12

{spec, probe}：编码器的预处理参数（以及权重所在位置），加上它对一张固定测试图的向量
（app.clients.inference.Embedder.fingerprint）。检索把它和即将使用的编码器比较，所以改了
EMBEDDING_CONFIG 后再也不会去查询或写入另一个模型建的 collection；/api/embedding-models
按它登记、补齐和切换版本。

一个不带默认值、可以为空的列：只改元数据，不重写表，不长时间持锁。
之前登记的行保持 NULL，直到 API 记录在它上面启动的编码器的指纹
（在用几张已存图片核对它们的向量之后）。

降级会删除这一列：已记录的指纹会丢失（API 会重新记录）。
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
