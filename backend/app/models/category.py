"""类别：类别树、多语言名称、加热组。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, FetchedValue, ForeignKey, Identity, Index, Text, text, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import NameKind
from app.db.base import Base, TimestampMixin
from app.db.types import LtreeType, str_enum


class HeatingGroup(Base):
    """加热组。类别通过 heating_group_id 多对一映射过来，只在一体机场景随结果输出。"""

    __tablename__ = "heating_groups"
    __table_args__ = {"comment": "加热组"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, comment="加热组编码")
    name: Mapped[str] = mapped_column(comment="名称")
    description: Mapped[str | None] = mapped_column(comment="说明")


class Category(TimestampMixin, Base):
    """类别树节点。

    根节点是四个域：食材、菜品、甜点烘焙、饮品；形态大类是菜品下的一层节点。
    层级深度、是否叶子、所属的域不单独存，查询时由 path 算出。
    path 由数据库维护（初始迁移里启用 ltree 扩展并建维护 path 的触发器）。
    """

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index("ix_categories_path_gist", "path", postgresql_using="gist"),
        Index("ix_categories_cuisines_gin", "cuisines", postgresql_using="gin"),
        {"comment": "类别树"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(
        unique=True, comment="稳定编码，如 dish.pizza；配置和规则里用它引用类别"
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True, comment="父节点，根节点为空",
    )
    path: Mapped[str] = mapped_column(
        LtreeType(), unique=True, server_default=FetchedValue(), server_onupdate=FetchedValue(),
        comment="ID 链，如 2.15.137；插入或移动节点时自动重算整棵子树，不手动写",
    )
    cuisines: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), comment="菜系，如 {american,italian}；子孙沿用祖先的菜系"
    )
    heating_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("heating_groups.id", ondelete="SET NULL"),
        index=True,
        comment="加热组；可设在任意节点，子孙没设时沿用最近祖先的",
    )
    sort_order: Mapped[int] = mapped_column(server_default=text("0"), comment="同级排序")
    is_active: Mapped[bool] = mapped_column(server_default=true(), comment="停用后不再用于标注")

    parent: Mapped[Category | None] = relationship(back_populates="children", remote_side="Category.id")
    # 有子节点时数据库拒绝删除（RESTRICT），ORM 不会把子节点改成根节点
    children: Mapped[list[Category]] = relationship(back_populates="parent", passive_deletes="all")
    names: Mapped[list[CategoryName]] = relationship(
        back_populates="category", cascade="all, delete-orphan", passive_deletes=True
    )
    heating_group: Mapped[HeatingGroup | None] = relationship()


class CategoryName(Base):
    """类别名称：每种语言一个主名，可有多个别名；加语言不用改表。"""

    __tablename__ = "category_names"
    __table_args__ = (
        Index(
            "uq_category_names_one_primary",
            "category_id",
            "lang",
            unique=True,
            postgresql_where=text("kind = 'primary'"),
        ),
        Index("ix_category_names_lang_name", "lang", "name"),
        {"comment": "类别名称（多语言）"},
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True, comment="所属类别"
    )
    lang: Mapped[str] = mapped_column(primary_key=True, comment="语言代码：zh、en、fr、ja……")
    name: Mapped[str] = mapped_column(primary_key=True, comment="名称")
    kind: Mapped[NameKind] = mapped_column(str_enum(NameKind, "kind"), comment="primary 主名 / alias 别名")

    category: Mapped[Category] = relationship(back_populates="names")
