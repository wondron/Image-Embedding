from __future__ import annotations

import asyncio
import inspect
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import httpx
from fastapi import Request

from app.bootstrap.write_gate import VectorWriteGate
from app.core import errors as err
from app.core.config.settings import Settings

if TYPE_CHECKING:
    from app.clients.ground_dino import GdinoDetect
    from app.clients.recipe_classifier import RecipeClassifier
    from app.core.config.nacos import NacosConfigProvider
    from app.services.predict.food_service import FoodService
    from app.services.predict.pipeline.a_onnx_segment import OnnxSegmentPy
    from app.bootstrap.catalog import CatalogServices
    from app.bootstrap.consistency import ConsistencyChecker
    from app.bootstrap.model_registry import ModelRegistry
    from app.bootstrap.reconciler import BackgroundReconciler
    from app.bootstrap.retrieval_stack import RetrievalStack


@dataclass(slots=True)
class ApplicationState:
    """单个 FastAPI 应用实例持有的配置、服务和共享资源。"""

    config: dict[str, Any]
    config_info: dict[str, Any]
    http_client: httpx.AsyncClient
    settings: Settings
    started_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_monotonic: float = field(default_factory=time.monotonic)
    food_service: FoodService | None = None
    # EMBEDDING_CONFIG 指定的编码器，供预测使用，并用于登记新模型版本和生成向量。
    # 检索服务可能持有另一个编码器，与其当前模型版本保持配对。
    embedder: Any = None
    detector: GdinoDetect | None = None
    detector_error: str | None = None
    # 业务搜图的目标检测（区域搜索和菜谱识别共用，app.api.services.region_search）轮流使用 GroundingDINO：
    # 在事件循环上排队，排队时被取消的请求不会再占用模型；正在推理的那次跑完才轮到下一个
    detector_turn: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    # RECIPE_DETECT_CONFIG 的菜谱识别模型，供业务搜图使用；没有这一节或 enabled 为 false 时不加载。
    # 加载、预热失败只记在 recipe_classifier_error，不影响就绪状态和其他接口
    recipe_classifier: RecipeClassifier | None = None
    recipe_classifier_error: str | None = None
    # SEGMENT_CONFIG 的分割模型，供 /api/tools/segment 使用；没有这一节或 enabled 为 false 时不加载。
    # 加载、预热失败只记在 segmentor_error，不影响就绪状态和其他接口
    segmentor: OnnxSegmentPy | None = None
    segmentor_error: str | None = None
    initialization_error: str | None = None
    warmup_error: str | None = None
    retrieval: Optional["RetrievalStack"] = None
    retrieval_error: str | None = None
    retrieval_error_code: str | None = None
    # 类别和场景管理仅依赖 PostgreSQL，检索栈会复用其存储
    catalog: Optional["CatalogServices"] = None
    catalog_error: str | None = None
    # RECONCILE_CONFIG 控制的后台对账任务：补推落后的向量载荷，并处理删除队列
    reconciler: Optional["BackgroundReconciler"] = None
    # /api/embedding-models 使用的模型版本管理：登记、生成向量、切换和下线
    models: Optional["ModelRegistry"] = None
    # /api/consistency 使用的数据核对：PostgreSQL、Qdrant、对象存储和模型版本之间是否一致（只读）
    consistency: Optional["ConsistencyChecker"] = None
    # 写入新向量时持有共享锁，切换模型版本时持有独占锁
    vector_writes: VectorWriteGate = field(default_factory=VectorWriteGate, repr=False)
    stopping: bool = False
    config_provider: NacosConfigProvider | None = None
    configuration_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False
    )

    @property
    def http_timeout(self) -> float:
        return self.settings.predict.http_timeout

    @property
    def max_image_bytes(self) -> int:
        return self.settings.predict.max_image_bytes

    @property
    def ready(self) -> bool:
        service = self.food_service
        return (
            not self.stopping
            and self.initialization_error is None
            and self.warmup_error is None
            and service is not None
            and getattr(service, "embeddingTool", None) is not None
        )

    def require_food_service(self) -> FoodService:
        with self.configuration_lock:
            if self.food_service is None:
                detail = self.initialization_error or "food service is not initialized"
                raise err.not_ready(detail)
            return self.food_service

    def require_retrieval(self) -> "RetrievalStack":
        with self.configuration_lock:
            if self.retrieval is None:
                detail = self.retrieval_error or "retrieval stack is not initialized"
                raise err.not_ready(detail)
            return self.retrieval

    def require_detector(self) -> GdinoDetect:
        with self.configuration_lock:
            if self.stopping or self.detector is None:
                detail = self.detector_error or "GroundingDINO is disabled or not initialized"
                raise err.not_ready(detail)
            return self.detector

    def require_segmentor(self) -> OnnxSegmentPy:
        with self.configuration_lock:
            if self.stopping or self.segmentor is None:
                detail = self.segmentor_error or "segment model is disabled or not initialized"
                raise err.not_ready(detail)
            return self.segmentor

    def require_recipe_classifier(self) -> RecipeClassifier:
        with self.configuration_lock:
            if self.stopping or self.recipe_classifier is None:
                detail = self.recipe_classifier_error or "recipe model is disabled or not initialized"
                raise err.not_ready(detail)
            return self.recipe_classifier

    def require_catalog(self) -> "CatalogServices":
        with self.configuration_lock:
            if self.catalog is None:
                detail = self.catalog_error or "catalog is not initialized"
                raise err.not_ready(detail)
            return self.catalog

    def replace_configuration(
        self,
        config: dict[str, Any],
        config_info: dict[str, Any],
        food_service: FoodService,
        embedder: Any = None,
        *,
        detector: GdinoDetect | None = None,
        segmentor: OnnxSegmentPy | None = None,
        recipe_classifier: RecipeClassifier | None = None,
    ) -> None:
        """原子更新已准备好的配置、服务和共享模型实例。"""
        with self.configuration_lock:
            self.config = config
            self.config_info = config_info
            self.food_service = food_service
            if embedder is not None:
                self.embedder = embedder
            self.detector = detector
            self.detector_error = None
            self.segmentor = segmentor
            self.segmentor_error = None
            self.recipe_classifier = recipe_classifier
            self.recipe_classifier_error = None
            self.initialization_error = None
            self.warmup_error = None

    def replace_retrieval(self, stack: "RetrievalStack") -> Optional["RetrievalStack"]:
        """切换模型版本时发布新的检索栈，并返回被替换的旧栈。

        已获取旧栈的请求继续使用原实例，由调用方在这些请求结束后关闭旧栈。
        """
        with self.configuration_lock:
            old, self.retrieval = self.retrieval, stack
            self.retrieval_error = None
            self.retrieval_error_code = None
            return old

    def record_config_reload_error(self, error: str) -> None:
        with self.configuration_lock:
            self.config_info = {
                **self.config_info,
                "last_reload_error": error,
                "last_reload_attempt_at": datetime.now()
                .astimezone()
                .isoformat(timespec="seconds"),
            }

    def runtime_info(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_s": round(time.monotonic() - self.started_monotonic, 2),
            "now": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    async def close(self) -> None:
        """停止后台任务，并依次释放应用状态持有的资源。"""
        with self.configuration_lock:
            self.stopping = True
        reconciler, self.reconciler = self.reconciler, None
        if reconciler is not None:  # 先停止对账任务，再关闭它使用的检索栈
            await asyncio.to_thread(reconciler.stop)
        models, self.models = self.models, None
        if models is not None:  # 正在执行的向量构建任务会在下一批次前停止
            await asyncio.to_thread(models.close)
        consistency, self.consistency = self.consistency, None
        if consistency is not None:  # 正在跑的核对在下一批之前停下
            await asyncio.to_thread(consistency.close)
        if self.config_provider is not None:
            self.config_provider.close()
        service = self.food_service
        retrieval, self.retrieval = self.retrieval, None
        catalog, self.catalog = self.catalog, None
        try:
            service_close = getattr(service, "close", None)
            if callable(service_close):
                result = service_close()
                if inspect.isawaitable(result):
                    await result
        finally:
            self.food_service = None
            try:
                if retrieval is not None:
                    retrieval.close()  # 先关闭检索栈，再关闭它借用的类别管理存储
            finally:
                try:
                    if catalog is not None:
                        catalog.close()
                finally:
                    try:
                        await self.http_client.aclose()
                    finally:
                        self.detector = None
                        self.segmentor = None
                        self.recipe_classifier = None


def get_application_state(request: Request) -> ApplicationState:
    """获取当前 FastAPI 应用已初始化的运行状态。"""
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApplicationState):
        raise err.not_ready("application state is not initialized; lifespan must be running")
    return runtime
