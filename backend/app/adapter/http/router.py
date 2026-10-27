from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.adapter.http.endpoints.health import router as health_router
from app.adapter.http.endpoints.image_download import router as image_download_router
from app.adapter.http.endpoints.predict import router as predict_router
from app.adapter.http.endpoints.tools import router as tools_router
from app.adapter.http.endpoints.retrieval import router as retrieval_router
router = APIRouter()

router.include_router(health_router)
router.include_router(image_download_router)
router.include_router(predict_router)
router.include_router(tools_router)
router.include_router(retrieval_router)


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")

