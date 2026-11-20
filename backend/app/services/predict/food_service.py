"""只用于预测的 FoodService 门面（旧的推理模块）。

DDD D6：由 adapter.http 的预测路由通过 bootstrap 的状态使用。
不能被 application.retrieval 或 retrieval_stack 导入。
流水线的各个阶段在 domain.pipeline 下（预测模块）。
"""

import logging, cv2
import numpy as np
import app.utils.timer as timerUtils
from app.services.predict.pipeline.b_getGlobalData import GetGlobalData
from app.services.predict.pipeline.c_embedding import GlobalFilter
import uuid


logger = logging.getLogger("food_service")

def is_uuid(value: str) -> bool:
    """
    判断字符串是否为合法 UUID
    """
    if not isinstance(value, str):
        return False
    try:
        uuid_obj = uuid.UUID(value)
        return str(uuid_obj) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False
    

class FoodService:
    def __init__(self, cfg, embedding):
        """embedding：进程内共用的 Embedder（由 bootstrap.lifespan 构建）。

        每次重新加载配置都会新建 FoodService，所以这里不加载模型：分割模型由 lifespan 单独
        管理（ApplicationState.segmentor），配置没变时重新加载也不会再读一遍权重。
        """
        self.searchTool = GetGlobalData(cfg)
        self.searchFilter = GlobalFilter(cfg, embedding)
        self.embeddingTool = embedding


    def detect(self, image_data: bytes, search_num: int = None, machine_info: str = "", trace_id: str = "", save_result: bool = False, timer: timerUtils.timer_tool = None):
        """timer：接口的计时器，这里记录 global搜索 / 后处理 两步，耗时由接口入口统一输出。"""
        timer = timer or timerUtils.timer_tool()

        img_bgr = None
        if isinstance(image_data, bytes):
            buf = np.frombuffer(image_data, dtype=np.uint8)
            img_bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        with timer.step("global搜索"):
            res = self.searchTool.detect(image_data, search_num)

        with timer.step("后处理"):
            documents = res['results'] if isinstance(res, dict) else res
            filtered = self.searchFilter.detect(img_bgr, documents)
            if isinstance(res, dict):
                res['results'] = filtered
                res['result_count'] = len(filtered)
            else:
                res = filtered
        return res
    

if __name__ == '__main__':
    from app.clients.inference import Embedder
    from app.core.config.store import get_cfg
    from app.core.observability.logging import setup_logger
    
    setup_logger()
    config = get_cfg()

    image_path = '/data/wangzhuo/01-code/02-project/mainfooddetect/config/images/segment_0902_0892.jpg'
    with open(image_path, 'rb') as f:
        image_data = f.read()
        food_service = FoodService(config, Embedder(config))
        res = food_service.detect(image_data, None, 'test', False)
        logger.info('res: %s', res)
