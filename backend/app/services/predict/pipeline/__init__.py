"""Predict / FoodService inference pipeline (legacy island).

DDD D6 — dependency boundary:

- This package is **for predict HTTP only** (FoodService → a/b/c/d stages).
- Retrieval (app.services.retrieval + bootstrap.retrieval_stack +
  app.api retrieval) must **not** import this package.
- Composition root (bootstrap.lifespan / application) may construct both
  FoodService and RetrievalStack; that is the only allowed join point. It also
  builds the one Embedder (app.clients.inference) both sides share — stages
  here receive it, they never load ONNX models themselves.
- FlyB is out of scope for retrieval.

Do not treat modules here as shared domain for image-search index/search/list.
"""
