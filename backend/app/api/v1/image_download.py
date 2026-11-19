"""下载公网图片的 HTTP 接口。"""

from fastapi import APIRouter, Query, Response
from starlette.concurrency import run_in_threadpool

from app.clients import public_image


router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/download", response_class=Response)
async def download_image(url: str = Query(..., min_length=1, max_length=8192)) -> Response:
    """为被源站 CORS 拦住的浏览器下载返回校验过的图片字节。"""
    content, media_type = await run_in_threadpool(public_image.fetch_image, url)
    return Response(content=content, media_type=media_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
