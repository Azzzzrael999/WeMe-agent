import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo


class JsonFormatter(logging.Formatter):
    def __init__(self, timezone: str) -> None:
        super().__init__()
        self._timezone = ZoneInfo(timezone)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(self._timezone).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, timezone: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(timezone))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
