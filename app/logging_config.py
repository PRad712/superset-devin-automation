import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", message),
            "message": message,
        }
        fields = getattr(record, "fields", {})
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, event_name: str, **fields: Any) -> None:
    logger.info(event_name, extra={"event": event_name, "fields": fields})


def configure_logging(level: int | str = logging.INFO) -> None:
    formatter = JsonFormatter()
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    for handler in root.handlers:
        handler.setFormatter(formatter)
    root_handler = root.handlers[0]
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [root_handler]
        uvicorn_logger.setLevel(level)
        uvicorn_logger.propagate = False
