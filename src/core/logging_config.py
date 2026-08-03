"""Process logging configuration and correlation context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .data_paths import PROJECT_ROOT
from .settings import LoggingSettings


@dataclass(frozen=True, slots=True)
class LogContext:
    run_id: str | None = None
    request_id: str | None = None
    operation: str | None = None


_LOG_CONTEXT: ContextVar[LogContext] = ContextVar(
    "application_log_context",
    default=LogContext(),
)


@contextmanager
def log_context(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    operation: str | None = None,
) -> Iterator[None]:
    """Temporarily add correlation fields while preserving an outer context."""
    token = bind_log_context(
        run_id=run_id,
        request_id=request_id,
        operation=operation,
    )
    try:
        yield
    finally:
        reset_log_context(token)


def bind_log_context(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    operation: str | None = None,
) -> Token[LogContext]:
    """Bind correlation fields until the returned token is explicitly reset."""
    current = _LOG_CONTEXT.get()
    return _LOG_CONTEXT.set(
        replace(
            current,
            run_id=run_id if run_id is not None else current.run_id,
            request_id=request_id if request_id is not None else current.request_id,
            operation=operation if operation is not None else current.operation,
        )
    )


def reset_log_context(token: Token[LogContext]) -> None:
    """Restore the context captured before :func:`bind_log_context`."""
    _LOG_CONTEXT.reset(token)


class LoggingContextFilter(logging.Filter):
    """Attach correlation fields without overwriting explicit record extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _LOG_CONTEXT.get()
        for field_name in ("run_id", "request_id", "operation"):
            if not hasattr(record, field_name):
                setattr(record, field_name, getattr(context, field_name))
        return True


class JsonLogFormatter(logging.Formatter):
    """Serialize a stable JSON Lines record for machine processing."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "request_id": getattr(record, "request_id", None),
            "operation": getattr(record, "operation", None),
            "process_id": record.process,
            "thread": record.threadName,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ConsoleLogFormatter(logging.Formatter):
    """Render readable console output with correlation fields when present."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        fields = [
            f"{name}={value}"
            for name in ("run_id", "request_id", "operation")
            if (value := getattr(record, name, None)) is not None
        ]
        return f"{message} [{' '.join(fields)}]" if fields else message


def configure_logging(
    settings: LoggingSettings,
    *,
    level_override: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Replace root handlers with console and rotating JSON Lines outputs."""
    level_name = (level_override or settings.level).strip().upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if not isinstance(level, int):
        raise ValueError(f"unsupported log level: {level_name}")
    if settings.retention_days < 1:
        raise ValueError("log retention days must be positive")

    directory_value = settings.directory.strip()
    if not directory_value:
        raise ValueError("log directory must not be empty")
    log_directory = Path(directory_value).expanduser()
    if not log_directory.is_absolute():
        log_directory = project_root / log_directory
    log_directory = log_directory.resolve()
    log_directory.mkdir(parents=True, exist_ok=True)
    log_file = log_directory / "application.jsonl"

    context_filter = LoggingContextFilter()
    console_handler = logging.StreamHandler()
    console_handler.addFilter(context_filter)
    console_handler.setFormatter(
        ConsoleLogFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=settings.retention_days,
        encoding="utf-8",
        delay=True,
    )
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    previous_handlers = tuple(root_logger.handlers)
    root_logger.handlers.clear()
    for handler in previous_handlers:
        handler.close()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return log_file
