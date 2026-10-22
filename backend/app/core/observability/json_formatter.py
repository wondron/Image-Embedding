from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config.settings import settings


_STANDARD_LOG_RECORD_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}


def _json_default(value: Any) -> str:
    return str(value)


class CustomJsonFormatter(logging.Formatter):
    def __init__(self, fmt: str | None = None):
        super().__init__(fmt=fmt)
        self.tz = ZoneInfo("Asia/Shanghai")

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "index": getattr(record, "index", settings.logging.index),
            "time": datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "env": settings.application.env,
            "logger": record.name,
            "level": record.levelname,
            "thread": record.threadName,
            "class": f"{record.name}.{record.funcName}:{record.lineno}",
            "message": record.getMessage(),
            "traceId": getattr(record, "traceId", ""),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS:
                continue
            if key in log_record:
                continue
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                continue
            log_record[key] = value

        log_record["stack_trace"] = ""
        if record.exc_info:
            log_record["stack_trace"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(log_record, ensure_ascii=False, default=_json_default)
