# HTTP 层约定

```text
http/
├── endpoints/       # 路由声明、Query / Body / File 参数绑定、HTTP 响应封装
├── schemas/         # 按接口用途组织的 Pydantic 请求 / 响应模型
├── services/        # HTTP 处理流程、共享图片 I/O、诊断信息及错误映射
├── dependencies.py # 获取应用资源，将服务未就绪转换成 HTTP 503
├── router.py       # 路由注册
├── middleware.py
└── exception_handlers.py
```

依赖方向为 `endpoints → services / schemas → bootstrap / domain`。HTTP 参数、
上传文件和 `HTTPException` 留在 adapter 层；模型推理和检索算法继续由 `app.domain`
负责，领域代码不导入这里的路由、schema 或 HTTP service。

新增 JSON 输入、输出时，优先在对应的 `schemas/<用途>.py` 定义模型，再由路由引用。
当前 `PredictUrlBody`、`PredictResponse`、`PingResponse` 位于 `schemas/predict.py`，
`SegmentResponse`、`EmbeddingResponse` 位于 `schemas/tools.py`。检索响应的 `result`
保留上游文档列表或状态对象的原始结构；健康诊断中的动态运行信息继续使用字典，避免
响应校验丢失诊断字段。图片下载直接返回二进制 `Response`。

共享图片处理集中在 `services/image_io.py`：

- `read_file_bytes`：有界读取上传文件，校验空文件、大小及可选 MIME 类型；调用方负责关闭文件。
- `clean_url` / `read_url_bytes`：规范化检索 URL，复用应用 HTTP client，流式读取并执行大小限制和 HTTP 错误映射。
- `encode_png_base64`：将灰度或 RGB / BGR 数组编码为 PNG Base64。

工具接口与检索接口复用读取函数，并保留各自的 MIME 校验和错误文案。
`services/image_download.py` 专门负责浏览器下载回退，保留公网地址、DNS 绑定、
逐次重定向校验、下载期限和图片格式验证；不能用检索 URL 读取函数替代。

修改这些模块后，在 `backend` 目录执行对应的 `tests/test_*endpoints.py`、
`test_http_image_io.py`、`test_image_download.py` 和 `test_predict_upstream_errors.py`。
这些 HTTP 测试使用模拟服务和网络，无需加载 ONNX 模型或访问外部服务。
