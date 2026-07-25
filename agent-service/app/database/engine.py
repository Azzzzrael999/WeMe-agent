from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().agent_database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
