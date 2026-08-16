import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api.internal import router as internal_router
from app.api.knowledge import router as knowledge_router
from app.config import get_settings
from app.logging import configure_logging
from app.rag.embeddings import EmbeddingProvider, build_embedding_provider

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.app_timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        keepalive_task: asyncio.Task[None] | None = None
        if settings.rag_embedding_provider == "bge_m3":
            logger.info("Preloading local BGE-M3 embedding provider")
            provider = build_embedding_provider(settings)
            await run_in_threadpool(provider.embed_query, "会议制度检索模型预热")
            logger.info("Local BGE-M3 embedding provider is ready")
            if settings.rag_keepalive_interval_seconds > 0:
                keepalive_task = asyncio.create_task(
                    _keep_embedding_warm(
                        provider, settings.rag_keepalive_interval_seconds
                    )
                )
        try:
            yield
        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await keepalive_task

    application = FastAPI(
        title="WeMe Agent Service",
        version="1.0.0",
        description="Internal multi-agent meeting orchestration and policy runtime.",
        lifespan=lifespan,
    )
    application.include_router(internal_router)
    application.include_router(knowledge_router)
    return application


app = create_app()


async def _keep_embedding_warm(
    provider: EmbeddingProvider, interval_seconds: int
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_in_threadpool(
                provider.embed_documents, ["会议制度检索模型保温"]
            )
            logger.debug("Local BGE-M3 embedding keepalive completed")
        except Exception:
            logger.warning("Local BGE-M3 embedding keepalive failed", exc_info=True)
