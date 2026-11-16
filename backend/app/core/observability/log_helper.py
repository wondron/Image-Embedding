from __future__ import annotations

import logging
import uuid
from typing import Any


def get_k8s_logger(
    trace_id: str = "",
    **kwargs: Any,
) -> logging.LoggerAdapter:
    extra = {
        "traceId": trace_id,
    }
    extra.update(kwargs)
    return logging.LoggerAdapter(logging.getLogger("platform"), extra=extra)


def show_text(
    message: str,
    trace_id: str = "",
    logger_level: int = logging.INFO,
    exc_info=None,
    **kwargs: Any,
) -> None:
    real_trace_id = trace_id or str(uuid.uuid4())
    logger = get_k8s_logger(
        trace_id=real_trace_id,
        **kwargs,
    )
    logger.log(logger_level, message, exc_info=exc_info)


def log_timing(
    logger: logging.Logger,
    title: str,
    timer: Any,
    *,
    trace_id: str,
    failed: bool = False,
    level: int | None = None,
    extra: str = "",
) -> None:
    """接口耗时的统一输出：一行「标题 | 各步骤耗时 | 总耗时 | 附加信息」，同时写 logger 和 show_text。

    timer：app.utils.timer.timer_tool，接口入口创建、各层记录步骤。
    failed：接口调用失败，标题后加「失败」，按 WARNING 输出；level 可另行指定级别。
    """
    if level is None:
        level = logging.WARNING if failed else logging.INFO
    message = " | ".join(part for part in (f"{title}失败" if failed else title, timer.summary(), extra) if part)
    logger.log(level, "%s | trace_id=%s", message, trace_id)
    show_text(message, trace_id=trace_id, logger_level=level)

#########################################################################
#使用方法：
# 1.常用方法：
# show_text("图片识别完成")
# show_text( "图片识别完成", trace_id="abc123", logger_level=logging.INFO, imageId="img001", spendTime="0.32s", model="segmentor", score=0.97,)
# 2.异常信息输出：
    #try:
        #1 / 0
    #except Exception:
        #show_text("执行失败", logger_level=logging.ERROR, exc_info=True, step="infer",)
# 3.接口耗时输出（logger 和 show_text 各写一行）：
    #timer = timer_tool()  # from app.utils.timer import timer_tool
    #with timer.step("图像下载"):
        #...
    #log_timing(logger, "自建库搜图(图片链接)", timer, trace_id=trace_id)
    # -> 自建库搜图(图片链接) | 图像下载 0.120s | embedding推理 0.045s | vector搜索 0.012s | 后处理 0.003s | 总耗时 0.185s
##########################################################################
