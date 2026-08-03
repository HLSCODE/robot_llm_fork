from __future__ import annotations

from io import StringIO
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.core.logging_config import (
    JsonLogFormatter,
    LoggingContextFilter,
    configure_logging,
    log_context,
)
from src.core.settings import LoggingSettings


class LoggingConfigurationTests(unittest.TestCase):
    def test_json_formatter_includes_bound_correlation_fields(self) -> None:
        output = StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(LoggingContextFilter())
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("tests.structured-logging")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            with log_context(
                run_id="run-42",
                request_id="request-7",
                operation="execution.run",
            ):
                logger.info("execution started")
        finally:
            logger.removeHandler(handler)
            logger.propagate = True

        payload = json.loads(output.getvalue())
        self.assertEqual("execution started", payload["message"])
        self.assertEqual("run-42", payload["run_id"])
        self.assertEqual("request-7", payload["request_id"])
        self.assertEqual("execution.run", payload["operation"])

    def test_nested_context_is_restored_after_scope_exit(self) -> None:
        records: list[logging.LogRecord] = []

        class RecordHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = RecordHandler()
        handler.addFilter(LoggingContextFilter())
        logger = logging.getLogger("tests.logging-context")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            with log_context(run_id="outer"):
                with log_context(operation="inner"):
                    logger.info("nested")
                logger.info("outer")
            logger.info("unbound")
        finally:
            logger.removeHandler(handler)
            logger.propagate = True

        self.assertEqual("outer", records[0].run_id)
        self.assertEqual("inner", records[0].operation)
        self.assertEqual("outer", records[1].run_id)
        self.assertIsNone(records[1].operation)
        self.assertIsNone(records[2].run_id)

    def test_configure_logging_creates_daily_retained_json_handler(self) -> None:
        root_logger = logging.getLogger()
        original_handlers = tuple(root_logger.handlers)
        original_level = root_logger.level
        with TemporaryDirectory() as temporary_directory:
            try:
                log_file = configure_logging(
                    LoggingSettings(
                        level="WARNING",
                        directory="logs",
                        retention_days=21,
                    ),
                    project_root=Path(temporary_directory),
                )
                file_handlers = [
                    handler
                    for handler in root_logger.handlers
                    if isinstance(handler, TimedRotatingFileHandler)
                ]
                logging.getLogger("tests.file-logging").warning("persisted")
                for handler in root_logger.handlers:
                    handler.flush()

                self.assertEqual(Path(temporary_directory) / "logs" / "application.jsonl", log_file)
                self.assertEqual(1, len(file_handlers))
                self.assertEqual(21, file_handlers[0].backupCount)
                payload = json.loads(log_file.read_text(encoding="utf-8"))
                self.assertEqual("persisted", payload["message"])
                self.assertEqual("WARNING", payload["level"])
            finally:
                configured_handlers = tuple(root_logger.handlers)
                root_logger.handlers.clear()
                for handler in configured_handlers:
                    handler.close()
                root_logger.handlers.extend(original_handlers)
                root_logger.setLevel(original_level)

    def test_invalid_logging_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "retention"):
            configure_logging(LoggingSettings(retention_days=0))


if __name__ == "__main__":
    unittest.main()
