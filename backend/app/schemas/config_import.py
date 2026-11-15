"""Configuration import wire: POST /api/categories/import, POST /api/scenes/import.

The request body is the file itself (a JSON object): what the admin pages download, or the body of
GET /api/categories / GET /api/scenes, possibly edited (app.services.retrieval.import_categories,
import_scenes). Both answer with the same report: counts, and one row per item that was (dry_run:
would be) created, updated or deleted, or that could not be imported.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.categories import CategorySceneSync

DRY_RUN_DOC = (
    "true（默认）：只预览，不写库，返回将要发生的变更；false：真正写入。建议先预览、确认无误后再用 false 提交"
)
PRUNE_DOC = (
    "只对场景导入有效。false（默认）：文件里没有列出的规则和场景类别保持不动；"
    "true：对文件里列出的场景，删除文件里没有列出的规则（该场景带 rules 列表时）和场景类别（带 labels 列表时）。"
    "场景本身从不删除"
)


class ImportChange(BaseModel):
    field: str = Field(..., description="变更的字段，如 names、cuisines、sort_order、label")
    before: str | None = Field(default=None, description="原值（新建时为 null）")
    after: str | None = Field(default=None, description="新值（清空时为 null）")


class ImportRow(BaseModel):
    kind: Literal["category", "scene", "label", "rule"] = Field(..., description="这一行是什么")
    key: str = Field(
        ...,
        description=(
            "对象标识：类别是编码；场景是编码；场景类别是「场景编码/类别编码」；"
            "规则是「场景编码/category:类别编码」或「场景编码/cuisine:菜系」"
        ),
    )
    action: Literal["create", "update", "delete", "error"] = Field(
        ..., description="create 新建 / update 更新 / delete 删除（仅 prune）/ error 这一项没有导入，原因见 error、detail"
    )
    name: str | None = Field(default=None, description="名称（有的话），给人看")
    scene: str | None = Field(default=None, description="所属场景编码（场景导入）")
    where: str | None = Field(default=None, description="在文件里的位置，如 categories[0].children[2]")
    changes: list[ImportChange] = Field(default_factory=list, description="create / update 时的字段变更")
    error: str | None = Field(default=None, description="action=error 时的错误码，如 VALIDATION_ERROR")
    detail: str | None = Field(default=None, description="action=error 时的说明")


class ImportSummary(BaseModel):
    total: int = Field(..., description="文件里列出的条目数（类别；或场景 + 场景类别 + 规则）")
    create: int
    update: int
    delete: int
    unchanged: int = Field(..., description="和库里一致、不需要改的条目数（不出现在 rows 里）")
    error: int
    not_in_file: int = Field(..., description="库里有、文件里没有列出的类别（或场景）数；导入不会动它们")


class ImportLeftover(BaseModel):
    id: int
    code: str
    name: str | None = None


class _ImportResponse(BaseModel):
    ok: bool = True
    dry_run: bool = Field(..., description="true：只是预览，什么都没有写")
    summary: ImportSummary
    rows: list[ImportRow] = Field(..., description="有变更或有问题的条目；最多 1000 行，有问题的优先保留")
    rows_truncated: bool = Field(default=False, description="rows 超过上限被截断（summary 仍是完整计数）")
    not_in_file: list[ImportLeftover] = Field(
        default_factory=list, description="库里有、文件里没有列出的类别（或场景），最多列 200 个"
    )


class CategoryImportResponse(_ImportResponse):
    scene_sync: CategorySceneSync | None = Field(
        default=None,
        description="写入时改了菜系才有：其下已入库图片的场景归属重算并同步到向量库的结果（同 PATCH /api/categories/{id}）",
    )


class SceneImportResponse(_ImportResponse):
    prune: bool = Field(..., description="本次请求的 prune")
    sync_required: bool = Field(
        ...,
        description=(
            "true：这些变更要「重算场景归属」（POST /api/scenes/sync）后才作用到已入库图片；"
            "预览时是写入后会得到的值"
        ),
    )
