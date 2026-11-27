"""服务的全部错误码，以及携带错误码的异常。

ERROR_CODES 是唯一的登记表：错误码 -> HTTP 状态码，以及 detail 能不能给客户端看。
通过下面的构造函数抛出 AppError（已登记的错误码也可以直接 AppError(code, detail)）；
app.core.exceptions 把每个失败都渲染成同一种结构：

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

detail 规则：登记了公开信息的错误码包装的是内部失败（数据库、向量库、模型），
它们的 detail 可能带异常文本、SQL 或路径。客户端拿到公开信息；日志里保留 detail
和原因，并带上 trace id。其他错误码的 detail 是写给客户端看的。不依赖框架：
任何一层都可以抛出这些错误。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    http_status: int
    # detail 属于内部信息的错误码才设置：客户端拿到这段文字，而不是 detail。
    public_detail: str | None = None


ERROR_CODES: dict[str, ErrorSpec] = {
    # -- 通用；前六个也是框架自身 HTTP 错误使用的错误码
    "BAD_REQUEST": ErrorSpec(400),
    "UNAUTHORIZED": ErrorSpec(401),
    "FORBIDDEN": ErrorSpec(403),
    "NOT_FOUND": ErrorSpec(404),
    "METHOD_NOT_ALLOWED": ErrorSpec(405),
    "TOO_MANY_REQUESTS": ErrorSpec(429),
    "VALIDATION_ERROR": ErrorSpec(422),
    "NOT_READY": ErrorSpec(503),
    "INTERNAL_ERROR": ErrorSpec(500, "internal server error"),
    # -- 上传和图片 URL
    "EMPTY_FILE": ErrorSpec(400),
    "FILE_TOO_LARGE": ErrorSpec(413),
    "UNSUPPORTED_MEDIA_TYPE": ErrorSpec(415),
    "INVALID_URL": ErrorSpec(422),
    "FORBIDDEN_HOST": ErrorSpec(403),
    "DOWNLOAD_FAILED": ErrorSpec(502),
    "DOWNLOAD_TIMEOUT": ErrorSpec(504),
    # -- 上游服务（Global 搜图）
    "UPSTREAM_ERROR": ErrorSpec(502),
    "UPSTREAM_TIMEOUT": ErrorSpec(504),
    # -- 预测和诊断工具（直接在 app.api.services.predict / tools 里抛出）
    "PREDICT_FAILED": ErrorSpec(500, "image search failed"),
    "INVALID_IMAGE": ErrorSpec(422),
    "TOOL_FAILED": ErrorSpec(500, "tool failed"),
    # -- 检索：入库（契约 §4.2）
    "INDEX_EMPTY_REQUEST": ErrorSpec(400),
    "INDEX_BAD_IMAGE": ErrorSpec(422),
    "INDEX_BAD_EMBEDDING": ErrorSpec(422),
    "INDEX_UNKNOWN_SCENE": ErrorSpec(422),
    "INDEX_QDRANT_FAILED": ErrorSpec(502, "vector store write failed"),
    "INDEX_POSTGRES_FAILED": ErrorSpec(502, "metadata database error"),
    # -- 检索：搜索
    "SEARCH_BAD_IMAGE": ErrorSpec(422),
    "SEARCH_UNKNOWN_SCENE": ErrorSpec(422),
    "SEARCH_BAD_SCORE_THRESHOLD": ErrorSpec(422),
    "SEARCH_BAD_EMBEDDING": ErrorSpec(422),
    "SEARCH_QDRANT_FAILED": ErrorSpec(502, "vector search failed"),
    "SEARCH_POSTGRES_FAILED": ErrorSpec(502, "metadata lookup failed"),
    "VECTOR_DIM_MISMATCH": ErrorSpec(503),
    # -- 检索：图库列表
    "LIST_UNKNOWN_SCENE": ErrorSpec(422),
    "LIST_POSTGRES_FAILED": ErrorSpec(502, "metadata list query failed"),
    # -- 单张已存图片（GET / PATCH / DELETE /api/images/{image_id}）
    "IMAGE_NOT_FOUND": ErrorSpec(404),
    "IMAGE_FILE_UNAVAILABLE": ErrorSpec(502, "stored original could not be read"),
    # -- 枚举（config/param/enums.yaml，app.core.config.enum_store）
    "ENUM_NOT_FOUND": ErrorSpec(404),
    "ENUM_NOT_EDITABLE": ErrorSpec(403),
    "ENUM_VALUE_EXISTS": ErrorSpec(409),
    "ENUM_FILE_INVALID": ErrorSpec(503),
    "ENUM_FILE_WRITE_FAILED": ErrorSpec(500, "enum file write failed"),
    # -- 类别（类别树，app.repositories.categories）
    "CATEGORY_NOT_FOUND": ErrorSpec(404),
    "CATEGORY_CODE_EXISTS": ErrorSpec(409),
    "CATEGORY_IN_USE": ErrorSpec(409),
    "CATEGORY_POSTGRES_FAILED": ErrorSpec(502, "category database error"),
    # -- 场景（场景配置，app.repositories.scene_config）
    "SCENE_NOT_FOUND": ErrorSpec(404),
    "SCENE_CODE_EXISTS": ErrorSpec(409),
    "SCENE_RULE_EXISTS": ErrorSpec(409),
    "SCENE_IN_USE": ErrorSpec(409),
    "SCENE_PROTECTED": ErrorSpec(403),
    "SCENE_POSTGRES_FAILED": ErrorSpec(502, "scene database error"),
    # embedding 模型版本（/api/embedding-models）
    "EMBEDDING_MODEL_NOT_FOUND": ErrorSpec(404),
    "EMBEDDING_MODEL_EXISTS": ErrorSpec(409),
    "EMBEDDING_MODEL_STATE": ErrorSpec(409),
    "EMBEDDING_MODEL_INCOMPLETE": ErrorSpec(409),
    "EMBEDDING_ENCODER_UNAVAILABLE": ErrorSpec(409),
    "EMBEDDING_BUILD_RUNNING": ErrorSpec(409),
    "EMBEDDING_MODEL_MISMATCH": ErrorSpec(503),
    # 数据核对（/api/consistency）
    "CONSISTENCY_CHECK_RUNNING": ErrorSpec(409),
}

_UNREGISTERED_5XX = ERROR_CODES["INTERNAL_ERROR"].public_detail or "internal server error"


class AppError(Exception):
    """带稳定错误码的失败；http_status 默认取该错误码登记的状态码。"""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        http_status: int | None = None,
        show_text: str | None = None,
    ) -> None:
        super().__init__(detail)
        spec = ERROR_CODES.get(code)
        self.code = code
        self.detail = detail
        self.http_status = int(http_status if http_status is not None else (spec.http_status if spec else 500))
        # 只写日志的额外上下文；不会出现在给客户端的错误结构里
        self.show_text = show_text or ""

    @property
    def status_code(self) -> int:
        """和 Starlette 的 HTTPException 用同样的属性名。"""
        return self.http_status

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r}, {self.detail!r}, http_status={self.http_status})"


class NotReadyError(AppError):
    """NOT_READY（503）：某个资源还没就绪。缺了它也能继续的调用方捕获这个类，
    不用再检查 exc.code。
    """

    def __init__(self, detail: str) -> None:
        super().__init__("NOT_READY", detail)


def is_internal(exc: AppError) -> bool:
    """detail 必须只留在日志里时为 True（登记为内部错误，或者未登记的 5xx）。"""
    spec = ERROR_CODES.get(exc.code)
    if spec is None:
        return exc.http_status >= 500
    return spec.public_detail is not None


def client_detail(exc: AppError) -> str:
    """客户端能看到的 detail：内部错误码给公开信息，其他给 detail 本身。"""
    if not is_internal(exc):
        return exc.detail
    spec = ERROR_CODES.get(exc.code)
    return spec.public_detail if spec is not None and spec.public_detail else _UNREGISTERED_5XX


# -- 通用 ------------------------------------------------------------------------

def validation_error(detail: str) -> AppError:
    return AppError("VALIDATION_ERROR", detail)


def not_ready(detail: str) -> NotReadyError:
    return NotReadyError(detail)


def internal_error(detail: str) -> AppError:
    return AppError("INTERNAL_ERROR", detail)


# -- 上传和图片 URL --------------------------------------------------------------

def empty_file(detail: str) -> AppError:
    return AppError("EMPTY_FILE", detail)


def file_too_large(detail: str) -> AppError:
    return AppError("FILE_TOO_LARGE", detail)


def unsupported_media_type(detail: str) -> AppError:
    return AppError("UNSUPPORTED_MEDIA_TYPE", detail)


def invalid_url(detail: str) -> AppError:
    return AppError("INVALID_URL", detail)


def forbidden_host(detail: str) -> AppError:
    return AppError("FORBIDDEN_HOST", detail)


def download_failed(detail: str) -> AppError:
    """远程图片服务器出错；总是 502，不用远端返回的状态码。"""
    return AppError("DOWNLOAD_FAILED", detail)


def download_timeout(detail: str) -> AppError:
    return AppError("DOWNLOAD_TIMEOUT", detail)


# -- 上游服务 --------------------------------------------------------------------

def upstream_error(detail: str, *, http_status: int = 502) -> AppError:
    """http_status 可以是上游自己返回的网关状态码（502 / 503 / 504）。"""
    return AppError("UPSTREAM_ERROR", detail, http_status=http_status)


def upstream_timeout(detail: str) -> AppError:
    return AppError("UPSTREAM_TIMEOUT", detail)


# -- 检索：入库 ------------------------------------------------------------------

def index_empty_request(detail: str = "no file and no path") -> AppError:
    return AppError("INDEX_EMPTY_REQUEST", detail)


def index_bad_image(detail: str) -> AppError:
    return AppError("INDEX_BAD_IMAGE", detail)


def index_bad_embedding(detail: str) -> AppError:
    return AppError("INDEX_BAD_EMBEDDING", detail)


def index_unknown_scene(detail: str) -> AppError:
    return AppError("INDEX_UNKNOWN_SCENE", detail)


def index_qdrant_failed(detail: str) -> AppError:
    return AppError("INDEX_QDRANT_FAILED", detail)


def index_postgres_failed(detail: str) -> AppError:
    """元数据库写入或连接失败（入库、删除、场景、启动）。"""
    return AppError("INDEX_POSTGRES_FAILED", detail)


# -- 检索：搜索 ------------------------------------------------------------------

def search_bad_image(detail: str) -> AppError:
    return AppError("SEARCH_BAD_IMAGE", detail)


def search_unknown_scene(detail: str) -> AppError:
    return AppError("SEARCH_UNKNOWN_SCENE", detail)


def search_bad_score_threshold(detail: str) -> AppError:
    return AppError("SEARCH_BAD_SCORE_THRESHOLD", detail)


def search_bad_embedding(detail: str) -> AppError:
    return AppError("SEARCH_BAD_EMBEDDING", detail)


def search_qdrant_failed(detail: str) -> AppError:
    return AppError("SEARCH_QDRANT_FAILED", detail)


def search_postgres_failed(detail: str) -> AppError:
    return AppError("SEARCH_POSTGRES_FAILED", detail)


def vector_dim_mismatch(detail: str) -> AppError:
    return AppError("VECTOR_DIM_MISMATCH", detail)


# -- 检索：图库列表 --------------------------------------------------------------

def list_unknown_scene(detail: str) -> AppError:
    return AppError("LIST_UNKNOWN_SCENE", detail)


def list_postgres_failed(detail: str) -> AppError:
    return AppError("LIST_POSTGRES_FAILED", detail)


# -- 单张已存图片 ----------------------------------------------------------------

def image_not_found(detail: str) -> AppError:
    """images 表里没有这个 id 的行（已删除的图片也算不存在）。"""
    return AppError("IMAGE_NOT_FOUND", detail)


def image_file_unavailable(detail: str) -> AppError:
    """图片存下来的原图无法从对象存储读取（不存在，或存储出错）；detail 里可能有
    存储桶名和 key，所以客户端拿到的是公开信息。
    """
    return AppError("IMAGE_FILE_UNAVAILABLE", detail)


# -- 枚举 ------------------------------------------------------------------------

def enum_not_found(detail: str) -> AppError:
    return AppError("ENUM_NOT_FOUND", detail)


def enum_not_editable(detail: str) -> AppError:
    """系统枚举，或者设置了 editable: false 而锁定的可编辑枚举。"""
    return AppError("ENUM_NOT_EDITABLE", detail)


def enum_value_exists(detail: str) -> AppError:
    return AppError("ENUM_VALUE_EXISTS", detail)


def enum_file_invalid(detail: str) -> AppError:
    """枚举文件读不了或违反了规则；detail 会说明位置，方便修复的人定位。"""
    return AppError("ENUM_FILE_INVALID", detail)


def enum_file_write_failed(detail: str) -> AppError:
    return AppError("ENUM_FILE_WRITE_FAILED", detail)


# -- 类别 ------------------------------------------------------------------------

def category_not_found(detail: str) -> AppError:
    return AppError("CATEGORY_NOT_FOUND", detail)


def category_code_exists(detail: str) -> AppError:
    return AppError("CATEGORY_CODE_EXISTS", detail)


def category_in_use(detail: str) -> AppError:
    """拒绝删除：这个类别还有子类别、图片或场景规则。"""
    return AppError("CATEGORY_IN_USE", detail)


def category_postgres_failed(detail: str) -> AppError:
    return AppError("CATEGORY_POSTGRES_FAILED", detail)


# -- 场景 ------------------------------------------------------------------------

def scene_not_found(detail: str) -> AppError:
    """不存在的场景，或场景下不存在的标签 / 规则。"""
    return AppError("SCENE_NOT_FOUND", detail)


def scene_code_exists(detail: str) -> AppError:
    """场景 code，或某个场景内的标签 code，已经被占用。"""
    return AppError("SCENE_CODE_EXISTS", detail)


def scene_rule_exists(detail: str) -> AppError:
    return AppError("SCENE_RULE_EXISTS", detail)


def scene_in_use(detail: str) -> AppError:
    """拒绝删除：标签还被规则引用。"""
    return AppError("SCENE_IN_USE", detail)


def scene_protected(detail: str) -> AppError:
    """默认场景不允许的修改。"""
    return AppError("SCENE_PROTECTED", detail)


def scene_postgres_failed(detail: str) -> AppError:
    return AppError("SCENE_POSTGRES_FAILED", detail)


# -- embedding 模型版本 ----------------------------------------------------------

def embedding_model_not_found(detail: str) -> AppError:
    return AppError("EMBEDDING_MODEL_NOT_FOUND", detail)


def embedding_model_exists(detail: str) -> AppError:
    """配置的编码器，或者这个名称 + 版本，已经登记过了。"""
    return AppError("EMBEDDING_MODEL_EXISTS", detail)


def embedding_model_state(detail: str) -> AppError:
    """版本的状态不允许这个操作（例如下线当前正在使用的版本）。"""
    return AppError("EMBEDDING_MODEL_STATE", detail)


def embedding_model_incomplete(detail: str) -> AppError:
    """要切换到的版本缺少部分已存图片的向量，而且没有传 allow_missing。"""
    return AppError("EMBEDDING_MODEL_INCOMPLETE", detail)


def embedding_encoder_unavailable(detail: str) -> AppError:
    """这个进程里没有加载（也加载不了）这个版本的编码器。"""
    return AppError("EMBEDDING_ENCODER_UNAVAILABLE", detail)


def embedding_build_running(detail: str) -> AppError:
    return AppError("EMBEDDING_BUILD_RUNNING", detail)


def embedding_model_mismatch(detail: str) -> AppError:
    """编码器会写入或查询另一个模型建的 collection：检索拒绝这样配对。"""
    return AppError("EMBEDDING_MODEL_MISMATCH", detail)


def consistency_check_running(detail: str) -> AppError:
    """本进程已经在跑一次数据核对。"""
    return AppError("CONSISTENCY_CHECK_RUNNING", detail)


# 框架自身抛出的 HTTPException 对应的错误码（404 路由不存在、405 方法不允许等）
_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "NOT_READY",
    504: "UPSTREAM_TIMEOUT",
}


def code_for_status(status: int) -> str:
    return _STATUS_CODES.get(int(status), f"HTTP_{int(status)}")
