"""预测 / FoodService 推理流水线（旧模块）。

DDD D6——依赖边界：

- 这个包**只给预测的 HTTP 接口用**（FoodService → a/b/c/d 各阶段）。
- 检索（app.services.retrieval + bootstrap.retrieval_stack + app.api 的检索部分）
  **不能**导入这个包。
- 组合根（bootstrap.lifespan / application）可以同时构建 FoodService 和
  RetrievalStack；这是唯一允许的汇合点。它还构建两边共用的那一个 Embedder
  （app.clients.inference）——这里的各阶段接收它，自己从不加载 ONNX 模型。
- FlyB 不属于检索的范围。

不要把这里的模块当成图搜图入库 / 搜索 / 列表共用的领域模块。
"""
