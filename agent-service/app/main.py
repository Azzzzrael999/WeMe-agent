import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api.internal import router as internal_router
from app.api.knowledge import router as knowledge_router
from app.config import get_settings
from app.logging import configure_logging
from app.rag.embeddings import build_embedding_provider

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.app_timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.rag_embedding_provider == "bge_m3":
            logger.info("Preloading local BGE-M3 embedding provider")
            provider = build_embedding_provider(settings)
            await run_in_threadpool(provider.embed_query, "会议制度检索模型预热")
            logger.info("Local BGE-M3 embedding provider is ready")
        yield

    application = FastAPI(
        title="Meeting Agent Service",
        version="0.4.0",
        description="Day 4 structured multi-agent scheduling runtime.",
        lifespan=lifespan,
    )
    application.include_router(internal_router)
    application.include_router(knowledge_router)
    return application


app = create_app()
