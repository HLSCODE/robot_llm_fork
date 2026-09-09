from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

from src.observability.crash_diagnostics import (
    CrashDiagnostics,
    record_failure,
    record_stage,
)
from src.observability.logging_config import log_context


class CrashDiagnosticsTests(unittest.TestCase):
    def test_exceptions_and_stages_are_file_only_and_hooks_are_restored(self) -> None:
        previous = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        console = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(console):
            with CrashDiagnostics(Path(directory)) as diagnostics:
                with log_context(request_id="save-test", operation="trajectory.create_action"):
                    record_stage("action.persist.begin", action_id="test")
                    try:
                        raise ValueError("diagnostic probe")
                    except ValueError as error:
                        record_failure("action.persist.failed")
                        sys.excepthook(type(error), error, error.__traceback__)
                        threading.excepthook(SimpleNamespace(
                            exc_type=type(error), exc_value=error,
                            exc_traceback=error.__traceback__, thread=threading.current_thread(),
                        ))
                        sys.unraisablehook(SimpleNamespace(
                            exc_type=type(error), exc_value=error,
                            exc_traceback=error.__traceback__, err_msg="destructor",
                        ))
                diagnostics.dump_threads()
            records = [json.loads(line) for line in diagnostics.events_path.read_text(
                encoding="utf-8").splitlines()]
            self.assertTrue(any(r["request_id"] == "save-test" for r in records))
            exceptions = [r for r in records if "exception" in r]
            self.assertEqual(len(exceptions), 4)
            self.assertTrue(all("ValueError: diagnostic probe" in r["exception"] for r in exceptions))
            self.assertIn("test_crash_diagnostics.py", diagnostics.fatal_path.read_text(encoding="utf-8"))
        self.assertEqual(console.getvalue(), "")
        self.assertEqual(previous, (sys.excepthook, threading.excepthook, sys.unraisablehook))

    def test_qt_warning_is_captured_and_previous_handler_restored(self) -> None:
        from PySide6.QtCore import qInstallMessageHandler, qWarning
        from src.gui.diagnostics import capture_qt_messages

        forwarded = []

        def previous_handler(kind, context, message):
            forwarded.append(message)

        original = qInstallMessageHandler(previous_handler)
        try:
            with tempfile.TemporaryDirectory() as directory:
                with CrashDiagnostics(Path(directory)) as diagnostics:
                    with capture_qt_messages(diagnostics):
                        qWarning("diagnostic Qt probe")
                    qWarning("restored probe")
                self.assertIn("diagnostic Qt probe", diagnostics.events_path.read_text(encoding="utf-8"))
                self.assertEqual(forwarded, ["restored probe"])
        finally:
            qInstallMessageHandler(original)

    def test_exception_escape_is_recorded_without_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            diagnostics = CrashDiagnostics(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "escape"):
                with diagnostics:
                    raise RuntimeError("escape")
            self.assertIn("RuntimeError: escape", diagnostics.events_path.read_text(encoding="utf-8"))

    def test_recording_worker_logs_caught_failure(self) -> None:
        from src.gui.controllers.recording_operation import _RecordingWorker

        def fail():
            raise RuntimeError("SDK diagnostic probe")

        with tempfile.TemporaryDirectory() as directory:
            with CrashDiagnostics(Path(directory)) as diagnostics:
                worker = _RecordingWorker(fail)
                worker.run()
                self.assertIsInstance(worker.error, RuntimeError)
            records = diagnostics.events_path.read_text(encoding="utf-8")
            self.assertIn("recording.worker.failed", records)
            self.assertIn("RuntimeError: SDK diagnostic probe", records)
