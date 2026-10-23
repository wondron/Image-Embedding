from app.domain.common.flybSearch import FlyBSearch


class GetGlobalData:
    def __init__(self, config):
        self.searchTool = FlyBSearch(config)

    def detect(self, image_data):
        """返回上游搜索结果；图片下载和可用性验证由使用方按需执行。"""
        return self.searchTool.detect(image_data, None, None, None)
