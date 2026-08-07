"""
Utilities for silencing noisy local model libraries.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from io import StringIO
from types import TracebackType
from typing import Self


def suppress_output() -> "_RedirectBoth":
    return _RedirectBoth(StringIO())


class _RedirectBoth:
    def __init__(self, sink: StringIO) -> None:
        self._stdout: AbstractContextManager[StringIO] = redirect_stdout(sink)
        self._stderr: AbstractContextManager[StringIO] = redirect_stderr(sink)

    def __enter__(self) -> Self:
        self._stdout.__enter__()
        self._stderr.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stderr.__exit__(exc_type, exc, traceback)
        self._stdout.__exit__(exc_type, exc, traceback)
