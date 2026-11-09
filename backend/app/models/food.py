"""食物标注：每张图的食物信息，和图片分表存（同 image_embeddings 的做法）。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UpdatedAtMixin
from app.db.types import editable_enum

if TYPE_CHECKING:
    from app.models.image import Image


class ImageFood(UpdatedAtMixin, Base):
    """图片里食物的标注：状态、加工状态、个数和三种质量。

    一张图最多一行（主键就是 image_id），图片删除时一起删。整行和每个字段都是非必填：
    没有标注的图没有这一行，读的时候按空处理。只有 food_state 进 Qdrant payload。
    取值规则见 app.services.retrieval.food_fields。
    """

    __tablename__ = "image_foods"
    __table_args__ = (
        CheckConstraint("food_count > 0", name="food_count_positive"),
        CheckConstraint("total_mass_g > 0", name="total_mass_positive"),
        CheckConstraint("water_mass_g >= 0", name="water_mass_nonnegative"),
        CheckConstraint("main_ingredient_mass_g > 0", name="main_ingredient_mass_positive"),
        {"comment": "图片的食物标注"},
    )

    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    food_state: Mapped[str | None] = mapped_column(
        editable_enum(), comment="食物状态（生 / 熟 / 冷冻），取值见 config/param/enums.yaml"
    )
    food_processing: Mapped[str | None] = mapped_column(
        editable_enum(), comment="加工状态（切片、切块、整个、切丝等），取值见 config/param/enums.yaml"
    )
    food_count: Mapped[int | None] = mapped_column(comment="食物个数")
    total_mass_g: Mapped[int | None] = mapped_column(comment="总质量（克）")
    water_mass_g: Mapped[int | None] = mapped_column(comment="含水质量（克），可以为 0")
    main_ingredient_mass_g: Mapped[int | None] = mapped_column(comment="主食材质量（克）")

    image: Mapped[Image] = relationship(back_populates="food")
