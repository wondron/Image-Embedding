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
##########################################################################
