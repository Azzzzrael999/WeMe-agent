from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.config import get_settings
from app.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.app_timezone)

    application = FastAPI(
        title="Meeting Agent Service",
        version="0.4.0",
        description="Day 4 structured multi-agent scheduling runtime.",
    )
    application.include_router(internal_router)
    return application


app = create_app()
