from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "schedule_id": getattr(record, "schedule_id", None),
            "target_date": getattr(record, "target_date", None),
            "room": getattr(record, "room", None),
            "status": getattr(record, "status", None),
            "attempt_number": getattr(record, "attempt_number", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "error_code": getattr(record, "error_code", None),
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


class EventLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def event(self, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
        safe_fields = {key: value for key, value in fields.items() if key != "school_id"}
        summary = " ".join(
            f"{key}={value}" for key, value in safe_fields.items() if value is not None
        )
        message = f"{event}: {summary}" if summary else event
        self.logger.log(level, message, extra={"event": event, **safe_fields})


def configure_logging(log_directory: Path, *, verbose: bool = False) -> EventLogger:
    log_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hayden_booker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(console)
    file_handler = TimedRotatingFileHandler(
        log_directory / "hayden-booker.jsonl",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    return EventLogger(logger)
