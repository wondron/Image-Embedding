import base64, requests, logging, io, json
from PIL import Image
import logging, time
from app.core.observability.log_helper import show_text
import numpy as np
from app.domain.common.flybSearch import FlyBSearch


logger = logging.getLogger('b_globaldata')


class GetGlobalData:
    def __init__(self, config):
        self.searchTool = FlyBSearch(config)
    
    def detect(self, image_data):
        res = self.searchTool.detect(image_data, None, None, None)
        return res