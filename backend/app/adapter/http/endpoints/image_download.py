"""HTTP endpoint for downloading public raster images."""

from fastapi import APIRouter, Query, Response
from starlette.concurrency import run_in_threadpool

from app.adapter.http.services import image_download


router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/download", response_class=Response)
async def download_image(url: str = Query(..., min_length=1, max_length=8192)) -> Response:
    """Return validated image bytes for browser downloads blocked by source CORS."""
    content, media_type = await run_in_threadpool(image_download.fetch_image, url)
    return Response(content=content, media_type=media_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
