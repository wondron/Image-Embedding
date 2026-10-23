import logging

import cv2
import numpy as np
import requests
from app.domain.common.flybSearch import FlyBSearch


logger = logging.getLogger('b_globaldata')


class GetGlobalData:
    def __init__(self, config):
        self.searchTool = FlyBSearch(config)


    def processData(self, input_res):
        if isinstance(input_res, dict):
            results = self.processData(input_res.get('results') or [])
            input_res['results'] = results
            input_res['result_count'] = len(results)
            return input_res

        valid_items = []
        for item in input_res:
            image_url = item.get('image_url')
            image_data = self._download_image(image_url)
            if image_data is None:
                images = (item.get('details') or {}).get('images') or []
                for image in images:
                    thumbnail = image.get('thumbnail_url')
                    thumbnail_url = thumbnail.get('large') if isinstance(thumbnail, dict) else thumbnail
                    if not thumbnail_url or thumbnail_url == image_url:
                        continue
                    image_data = self._download_image(thumbnail_url)
                    if image_data is not None:
                        break

            if image_data is None:
                logger.warning('Removing item with no usable image: %s', image_url)
                continue
            item['image_bgr'] = image_data
            valid_items.append(item)

        input_res[:] = valid_items
        return input_res

    @staticmethod
    def _download_image(url):
        """Download an image as a uint8 HWC BGR array, or return None."""
        if not isinstance(url, str) or not url.strip():
            return None
        try:
            with requests.get(url, timeout=10) as response:
                response.raise_for_status()
                data = response.content
            if not data:
                return None
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                return None
            return image
        except (requests.RequestException, OSError, ValueError, cv2.error) as exc:
            logger.warning('Failed to download image %s: %s', url, exc)
            return None


    
    def detect(self, image_data):
        res = self.searchTool.detect(image_data, None, None, None)
        return self.processData(res)
