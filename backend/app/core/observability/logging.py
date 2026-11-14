from __future__ import annotations

import logging
import re
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


# Exact uvicorn 0.30.6 templates on uvicorn / uvicorn.error only. Applied to a
# LogRecord copy so the original record.msg / args / exc_info stay unchanged
# for any other handler. Access logs and other uvicorn messages are left as-is.
_UVICORN_LOGGERS = frozenset({"uvicorn", "uvicorn.error"})
_UVICORN_MSG_TEMPLATES = {
    "Started server process [%d]": "已启动服务器进程 [%d]",
    "Finished server process [%d]": "服务器进程已结束 [%d]",
    "Waiting for application startup.": "正在等待应用启动。",
    "Application startup failed. Exiting.": "应用启动失败，正在退出。",
    "Application startup complete.": "应用启动完成。",
    "Waiting for application shutdown.": "正在等待应用关闭。",
    "Application shutdown failed. Exiting.": "应用关闭失败，正在退出。",
    "Application shutdown complete.": "应用关闭完成。",
    "Uvicorn running on socket %s (Press CTRL+C to quit)": "Uvicorn 运行于 socket %s（按 CTRL+C 退出）",
    "Uvicorn running on unix socket %s (Press CTRL+C to quit)": "Uvicorn 运行于 unix socket %s（按 CTRL+C 退出）",
    "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)": "Uvicorn 运行于 %s://%s:%d（按 CTRL+C 退出）",
    "Uvicorn running on %s://[%s]:%d (Press CTRL+C to quit)": "Uvicorn 运行于 %s://[%s]:%d（按 CTRL+C 退出）",
    "Shutting down": "正在关闭",
}
# Reloader parent uses already-interpolated f-strings; match getMessage() only.
_UVICORN_RENDERED_PATTERNS = (
    (re.compile(r"^Started reloader process \[(\d+)\] using (.+)$"), r"已启动 reloader 进程 [\1]，使用 \2"),
    (re.compile(r"^Stopping reloader process \[(\d+)\]$"), r"正在停止 reloader 进程 [\1]"),
)


class CustomFormatter(logging.Formatter):
    def __init__(self, name_width: int = 12):
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
        record.name_fmt = _align(record.name, self.name_width, "center")
        target = record
        if record.name in _UVICORN_LOGGERS:
            target = logging.makeLogRecord(record.__dict__)
            if isinstance(target.msg, str) and target.msg in _UVICORN_MSG_TEMPLATES:
                target.msg = _UVICORN_MSG_TEMPLATES[target.msg]
            else:
                rendered = target.getMessage()
                for pat, repl in _UVICORN_RENDERED_PATTERNS:
                    new, n = pat.subn(repl, rendered, count=1)
                    if n:
                        target.msg = new
                        target.args = None
                        break
        return super().format(target)


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

    formatter = CustomFormatter(name_width=12)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

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
            platform_handler = TimedRotatingFileHandler(filename=str(platform_log_file), when="midnight", interval=1, backupCount=15, encoding="utf-8",)
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
