from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.api.v1.categories import router as categories_router
from app.api.v1.embedding_models import router as embedding_models_router
from app.api.v1.enums import router as enums_router
from app.api.v1.health import router as health_router
from app.api.v1.image_download import router as image_download_router
from app.api.v1.images import router as images_router
from app.api.v1.predict import router as predict_router
from app.api.v1.retrieval import router as retrieval_router
from app.api.v1.scene_memberships import router as scene_memberships_router
from app.api.v1.scenes import router as scenes_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.tools import router as tools_router
from app.schemas.common import ERROR_RESPONSES

router = APIRouter()

for sub_router in (
    health_router,
    image_download_router,
    predict_router,
    tools_router,
    retrieval_router,
    enums_router,
    categories_router,
    scenes_router,
    scene_memberships_router,
    images_router,  # after image_download_router: /api/images/{image_id} must not take /download
    tasks_router,
    embedding_models_router,
):
    router.include_router(sub_router, responses=ERROR_RESPONSES)


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")

