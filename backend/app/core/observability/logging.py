from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config.settings import settings
from app.core.observability.json_formatter import CustomJsonFormatter


def _align(text: str, width: int, align: str) -> str:
    if align == "left":
        return f"{text:<{width}}"
    if align == "right":
        return f"{text:>{width}}"
    return f"{text:^{width}}"


class CustomFormatter(logging.Formatter):
    def __init__(self, name_width: int = 16):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-4s | %(name_fmt)s | %(message)s",
            datefmt="%m-%d %H:%M:%S",
        )
        self.name_width = name_width
        self.tz = ZoneInfo("Asia/Shanghai")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")

    def format(self, record: logging.LogRecord) -> str:
        record.levelname = record.levelname[:4]
        record.name_fmt = _align(record.name, self.name_width, "left")
        return super().format(record)


def setup_logger(
    save_to_file: bool | None = None,
    save_k8s_file: bool | None = None,
) -> logging.Logger:
    if save_to_file is None:
        save_to_file = settings.logging.save_to_file
    if save_k8s_file is None:
        save_k8s_file = settings.logging.save_k8s_file

    logging.addLevelName(logging.DEBUG, "DBUG")
    logging.addLevelName(logging.INFO, "INFO")
    logging.addLevelName(logging.WARNING, "WARN")
    logging.addLevelName(logging.ERROR, "ERRO")
    logging.addLevelName(logging.CRITICAL, "CRIT")

    log_dir = Path(settings.paths.output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    app_log_file = log_dir / "app.log"
    platform_log_file = log_dir / "platform.log"

    formatter = CustomFormatter(name_width=16)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    # ===== 普通日志 =====
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if save_to_file:
        file_handler = TimedRotatingFileHandler(
            filename=str(app_log_file),
            when="midnight",
            interval=1,
            backupCount=15,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.suffix = "%Y-%m-%d"
        root_logger.addHandler(file_handler)

    # ===== 平台日志 =====
    platform_logger = logging.getLogger("platform")
    platform_logger.setLevel(logging.INFO)
    platform_logger.handlers.clear()
    platform_logger.propagate = False

    platform_enabled = save_k8s_file or save_to_file

    if platform_enabled:
        # 给 k8s / 日志平台采集：输出到控制台 stdout，格式为 JSON
        platform_console_handler = logging.StreamHandler()
        platform_console_handler.setFormatter(CustomJsonFormatter("%(message)s"))
        platform_logger.addHandler(platform_console_handler)

        # 如果还需要额外落文件，再保留文件 handler
        if save_to_file:
            platform_handler = TimedRotatingFileHandler(
                filename=str(platform_log_file),
                when="midnight",
                interval=1,
                backupCount=15,
                encoding="utf-8",
            )
            platform_handler.setFormatter(CustomJsonFormatter("%(message)s"))
            platform_handler.suffix = "%Y-%m-%d"
            platform_logger.addHandler(platform_handler)

    # ===== 清理 uvicorn / fastapi =====
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "starlette"]:
        l = logging.getLogger(name)
        l.handlers.clear()
        l.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    root_logger.info("日志系统初始化完成 | 路径=%s | save_to_file=%s | save_k8s_file=%s", log_dir, save_to_file, save_k8s_file,)
    return root_logger