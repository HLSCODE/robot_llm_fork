"""Process-scoped, file-only exception and fatal-error diagnostics."""

from __future__ import annotations

import faulthandler
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import Any
from uuid import uuid4

from .logging_config import JsonLogFormatter, LoggingContextFilter


_stages = logging.getLogger("process.diagnostic_stages")
_stages.setLevel(logging.INFO)
_stages.propagate = False
_stages.addHandler(logging.NullHandler())


def record_stage(stage: str, **fields: str | int | bool) -> None:
    """Emit small diagnostic checkpoints to the process file, never the console.

    Callers must not supply input text, credentials or complete action parameters.
    """
    fields.update(native_thread_id=threading.get_native_id(), monotonic_ns=time.monotonic_ns())
    _stages.info("%s %s", stage, json.dumps(fields, ensure_ascii=False))


def record_failure(stage: str, **fields: str | int | bool) -> None:
    """Record the current exception with its complete traceback in the file."""
    _stages.exception("%s %s", stage, json.dumps(fields, ensure_ascii=False))


class CrashDiagnostics:
    """Keep the fatal-output descriptor open until all application work stops.

    Fatal output is deliberately not rotated: faulthandler retains its file
    descriptor. Each launch gets separate files, including concurrent launches.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        identity = f"{os.getpid()}-{uuid4().hex}"
        self.events_path = directory / f"diagnostics-{identity}.jsonl"
        self.fatal_path = directory / f"fatal-{identity}.log"
        self._handler = logging.FileHandler(self.events_path, encoding="utf-8")
        self._handler.setFormatter(JsonLogFormatter())
        self._handler.addFilter(LoggingContextFilter())
        self.logger = logging.Logger("process.diagnostics", logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(self._handler)
        try:
            self._fatal = self.fatal_path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            self._handler.close()
            raise
        self._installed = False

    def __enter__(self) -> CrashDiagnostics:
        self._previous_sys = sys.excepthook
        self._previous_thread = threading.excepthook
        self._previous_unraisable = sys.unraisablehook
        self._fatal_was_enabled = faulthandler.is_enabled()
        # Windows VEH also observes recoverable COM first-chance exceptions.
        # Traversing other Python threads there can itself fault (observed on
        # CPython 3.12). Native debuggers still capture every native thread.
        fatal_all_threads = sys.platform != "win32"
        # Do not replace another owner's fatal descriptor (e.g. pytest/debugger).
        if not self._fatal_was_enabled:
            try:
                faulthandler.enable(file=self._fatal, all_threads=fatal_all_threads)
            except Exception:
                self._handler.close()
                self._fatal.close()
                raise
        sys.excepthook = self._exception
        threading.excepthook = self._thread_exception
        sys.unraisablehook = self._unraisable
        self._installed = True
        _stages.addHandler(self._handler)
        self._fatal.write(f"pid={os.getpid()} python={sys.version} platform={sys.platform}\n")
        fatal_threads = "external" if self._fatal_was_enabled else (
            "all" if fatal_all_threads else "current"
        )
        self.logger.info("diagnostics.started fatal_owned=%s fatal_threads=%s",
                         not self._fatal_was_enabled, fatal_threads)
        self.logger.info("runtime executable=%s python=%s platform=%s", sys.executable,
                         sys.version, sys.platform)
        for package in ("PySide6", "shiboken6", "tj-robot-proj"):
            try:
                installed = version(package)
            except PackageNotFoundError:
                installed = "not-installed"
            self.logger.info("runtime.package name=%s version=%s", package, installed)
        return self

    def _exception(
        self, kind: type[BaseException], value: BaseException, traceback: TracebackType | None,
    ) -> None:
        self.logger.critical("python.uncaught", exc_info=(kind, value, traceback))

    def _thread_exception(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        self.logger.critical(
            "thread.uncaught thread=%s", args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
            if args.exc_value is not None else None,
        )

    def _unraisable(self, args: Any) -> None:
        # Do not repr(args.object): it may be a partly destroyed Qt object.
        self.logger.error(
            "python.unraisable reason=%s", args.err_msg,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def dump_threads(self) -> None:
        """Explicit Python/GIL-held snapshot, separate from native fault callbacks."""
        faulthandler.dump_traceback(file=self._fatal, all_threads=True)

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> None:
        if kind is not None:
            self._exception(kind, value, traceback)
        if self._installed:
            sys.excepthook = self._previous_sys
            threading.excepthook = self._previous_thread
            sys.unraisablehook = self._previous_unraisable
            if not self._fatal_was_enabled:
                faulthandler.disable()
            self._installed = False
        _stages.removeHandler(self._handler)
        self.logger.info("diagnostics.closed normal_exit=%s", kind is None)
        self._handler.close()
        self._fatal.close()
