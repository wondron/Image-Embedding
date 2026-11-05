"""Predict / FoodService inference pipeline (legacy island).

DDD D6 — dependency boundary:

- This package is **for predict HTTP only** (FoodService → a/b/c/d stages).
- Retrieval (app.application.retrieval + bootstrap.retrieval_stack +
  adapter/http retrieval) must **not** import this package.
- Composition root (bootstrap.lifespan / application) may construct both
  FoodService and RetrievalStack; that is the only allowed join point.
- QdrantStore used by d_vector_index lives under
  app.adapter.persistence.qdrant_store (D5); FlyB is out of scope for retrieval.

Do not treat modules here as shared domain for image-search index/search/list.
"""
