"""Domain packages.

Two islands (DDD D6):

1. **retrieval** (`domain.retrieval` + `application.retrieval`) — image index /
   search / list / delete / scene recompute. Adapters: persistence, embedding
   port, HTTP retrieval. Must not import `domain.food_service` or
   `domain.pipeline`.

2. **predict / inference** (`domain.food_service` + `domain.pipeline` + shared
   `domain.common` helpers such as embedding/image_utils) — legacy FoodService
   predict routes only. Must not import `application.retrieval` or
   `adapter.persistence.pg_metadata`.

Composition root (`bootstrap`) may wire both. FlyB is not on the retrieval path.
"""
