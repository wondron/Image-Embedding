from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.api.v1.health import router as health_router
from app.api.v1.image_download import router as image_download_router
from app.api.v1.predict import router as predict_router
from app.api.v1.tools import router as tools_router
from app.api.v1.retrieval import router as retrieval_router
router = APIRouter()

router.include_router(health_router)
router.include_router(image_download_router)
router.include_router(predict_router)
router.include_router(tools_router)
router.include_router(retrieval_router)


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")

