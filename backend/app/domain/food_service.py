import logging, cv2
import numpy as np
import app.domain.common.timer_utils as timerUtils
from app.core.config.settings import settings
from app.core.observability.log_helper import show_text
from app.domain.pipeline.b_getGlobalData import GetGlobalData
from app.domain.pipeline.c_embedding import GlobalFilter
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
    def __init__(self, cfg):
        self.searchTool = GetGlobalData(cfg)
        self.searchFilter = GlobalFilter(cfg)


    def detect(self, image_data: bytes, machine_info: str = "", trace_id: str = "", save_result: bool = False):
        logger.info('start, trace_id: %s, save_result: %s, save_env: %s, machine_info: %s', trace_id, save_result, settings.paths.save_images_enabled, machine_info)
        timer_tools = timerUtils.timer_tool()
        
        img_bgr = None
        if isinstance(image_data, bytes):
            buf = np.frombuffer(image_data, dtype=np.uint8)
            img_bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        res = self.searchTool.detect(image_data)
        timer_tools.mark('global_search')

        documents = res['results'] if isinstance(res, dict) else res
        filtered = self.searchFilter.detect(img_bgr, documents)
        if isinstance(res, dict):
            res['results'] = filtered
            res['result_count'] = len(filtered)
        else:
            res = filtered
        timer_tools.mark('clip_dtct')

        timer_summary = timer_tools.summary()
        logger.info(timer_summary)
        show_text(timer_summary, trace_id)
        return res
    

if __name__ == '__main__':
    from app.core.config.store import get_cfg
    from app.core.observability.logging import setup_logger
    
    setup_logger()
    config = get_cfg()

    image_path = '/data/wangzhuo/01-code/02-project/mainfooddetect/config/images/segment_0902_0892.jpg'
    with open(image_path, 'rb') as f:
        image_data = f.read()
        food_service = FoodService(config)
        res = food_service.detect(image_data, 'test', False)
        logger.info('res: %s', res)
