from __future__ import annotations

from app.bootstrap.application import create_app
from app.core.config.settings import settings


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.application.host,
        port=settings.application.port,
        reload=settings.application.reload,
    )
