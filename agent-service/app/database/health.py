import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.engine import get_engine
from app.schemas.health import ComponentStatus

logger = logging.getLogger(__name__)


def probe_database() -> ComponentStatus:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database health probe failed (%s)", type(exc).__name__)
        return ComponentStatus.DOWN
    return ComponentStatus.UP
