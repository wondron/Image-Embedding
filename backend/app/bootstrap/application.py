from __future__ import annotations

from fastapi import FastAPI

from app.adapter.http.exception_handlers import register_exception_handlers
from app.adapter.http.middleware import register_middlewares
from app.adapter.http.router import router
from app.bootstrap.lifespan import lifespan
from app.core.config.settings import settings


def register_routes(app: FastAPI) -> None:
    app.include_router(router)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.application.service_name,
        version=settings.application.version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    register_middlewares(app)
    register_routes(app)
    register_exception_handlers(app)
    return app
