import base64, requests, logging, io, json
from PIL import Image
import logging, time
from app.core.observability.log_helper import show_text
logger = logging.getLogger('flyB')


class FlyBSearch:
    def __init__(self, config):
        self.load_config(config)
        show_str = f"flyb inited, url: {self.url}, thre: {self.thre}"
        logger.info(show_str)
        show_text(show_str)

    def load_config(self, config):
        config_flyb = config.get("FLAB_CONFIG", {})
        self.url = config_flyb['API_URL']
        self.key = config_flyb['API_KEY']
        self.doc_num = config_flyb['DOC_COUNT']
        self.max_lenth = config_flyb['MAX_SNIPPET_LENGTH']
        self.thre = config_flyb['THRESHOLD']
        self.enable = config_flyb['EnableWaiting']
        self.timeout = config_flyb['Timeout']

        if not 1 <= self.doc_num <= 20:
            raise ValueError("DOC_COUNT 必须在 1～20 之间")
        if not 1 <= self.max_lenth <= 3000:
            raise ValueError("MAX_SNIPPET_LENGTH 必须在 1～3000 之间")
        return
    
    
    def get_image_datas(self, image_data):
        if image_data is None:
            return None
        try:
            start_time = time.time()
            image = Image.open(io.BytesIO(image_data))
            image = image.convert('RGB')
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            encode_image = base64.b64encode(buffered.getvalue())
            encode_image_text = encode_image.decode("utf-8")
            base64_qwen = f"{encode_image_text}"
            end_time = time.time() - start_time
            logger.info('图像base转换时间：%s', end_time)
            return base64_qwen
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            return None
    
    
    def detect(self, image_data, query = None, roi = None, urls = None):
        is_url = True
        use_data = None
        if urls == None:
            is_url = False
            use_data = self.get_image_datas(image_data)
            
        if image_data is None and urls is None:
            return ""
        logger.info('启用 FLYB 检测')
        result = self.get_result(use_data, is_url, query, roi)
        return result


    def build_image_query(self, image_data, is_url, roi=None):
        if is_url:
            image_query = {"Url": image_data}
        else:
            image_query = {"ImageBase64": image_data}

        if roi is not None:
            x_min, y_min, x_max, y_max = roi
            if not (0 <= x_min < x_max <= 1 and 0 <= y_min < y_max <= 1):
                raise ValueError("ROI 坐标必须在 [0, 1] 范围内")
            image_query["RegionOfInterest"] = {"XMin": x_min, "YMin": y_min, "XMax": x_max, "YMax": y_max}
        return image_query
    

    def get_result(self, image_data=None, is_url=False, query=None, roi=None):
        if image_data is None:
            raise ValueError("search global 输入图像数据为空！")
        
        image_query = self.build_image_query(image_data, is_url, roi)
        payload = {
            "SearchType": "visual",
            "ImageQuery": image_query,
            "DocCount": self.doc_num,
            "MaxSnippetLength": self.max_lenth,
            "EnableWaiting": self.enable
        }
        if query:
            if len(query) > 100:
                raise ValueError("QUERY 最多 100 个字符")
            payload["Query"] = query
        
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",}
        response = requests.post(self.url, headers=headers, json=payload, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.warning("Global search upstream HTTP error | status=%s | request_id=%s", response.status_code, response.headers.get("X-Request-Id"),)
            raise
        data = response.json()
        return self.parse_results(data)


    def parse_results(self, data: dict) -> dict:
        """Parse documents into primary fields and grouped supporting details."""
        metadata = data.get("ResponseMetadata") or {}
        result = data.get("Result") or {}

        if metadata.get("Error"):
            raise RuntimeError(json.dumps(metadata["Error"], ensure_ascii=False))
        
        error_code = result.get("ErrorCode")
        if error_code not in (None, 0):
            error_msg = result.get("ErrorMsg") or ""
            if (
                error_code == 500
                and error_msg.startswith("Search engine response is empty, no results returned for this query.")
                and not result.get("Documents")
            ):
                request_id = metadata.get("RequestId")
                logger.warning("FLYB search engine returned an empty response | request_id=%s | error_code=%s | error_msg=%s", request_id, error_code, error_msg,)
                return {
                    "request_id": request_id,
                    "total_doc_count": 0,
                    "result_count": 0,
                    "results": [],
                    "status": "upstream_empty_response",
                    "upstream_error": {"code": error_code, "message": error_msg},
                }
            raise RuntimeError(f"搜索失败: {error_code}, {result.get('ErrorMsg', '')}")
        documents = []
        for doc in result.get("Documents") or []:
            text_snippets = []
            images = []
            for snippet in doc.get("Snippet") or []:
                if snippet.get("Type") == "text":
                    text = snippet.get("Text")
                    if text:
                        text_snippets.append(text)
                elif snippet.get("Type") == "image":
                    image = snippet.get("Image") or {}
                    images.append({
                        "image_url": image.get("ImageUrl"),
                        "thumbnail_url": image.get("ThumbnailUrl"),
                        "alt": image.get("Alt"),
                        "width": image.get("Width"),
                        "height": image.get("Height"),
                    })

            image_url = next((image["image_url"] for image in images if image["image_url"]), None,) or next((image["thumbnail_url"] for image in images if image["thumbnail_url"]), None,)
            documents.append({
                "title": doc.get("Title"),
                "image_url": image_url,
                "similiar": None,
                "details": {
                    "rank": doc.get("Rank"),
                    "page_url": doc.get("Url"),
                    "site": doc.get("HostInfo"),
                    "text_snippets": text_snippets,
                    "images": images,
                    "document_info": doc.get("DocumentInfo"),
                },
            })
        return documents