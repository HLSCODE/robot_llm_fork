from __future__ import annotations

import json
import logging
import sys

from src.observability.logging_config import ConsoleLogFormatter, JsonLogFormatter


def _exception_record() -> logging.LogRecord:
    try:
        raise RuntimeError("native initialization failed")
    except RuntimeError:
        return logging.getLogger("test.hardware").makeRecord(
            "test.hardware",
            logging.ERROR,
            __file__,
            1,
            "Hardware startup failed: %s",
            ("native initialization failed",),
            exc_info=sys.exc_info(),
        )


def test_console_formatter_omits_exception_traceback() -> None:
    output = ConsoleLogFormatter("%(levelname)s %(message)s").format(
        _exception_record()
    )

    assert output == "ERROR Hardware startup failed: native initialization failed"
    assert "Traceback" not in output


def test_json_formatter_preserves_exception_traceback() -> None:
    payload = json.loads(JsonLogFormatter().format(_exception_record()))

    assert payload["message"] == (
        "Hardware startup failed: native initialization failed"
    )
    assert "Traceback" in payload["exception"]
    assert "RuntimeError: native initialization failed" in payload["exception"]
