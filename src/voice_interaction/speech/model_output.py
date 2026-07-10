"""
Utilities for silencing noisy local model libraries.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO


def suppress_output():
    return _RedirectBoth(StringIO())


class _RedirectBoth:
    def __init__(self, sink: StringIO) -> None:
        self._stdout = redirect_stdout(sink)
        self._stderr = redirect_stderr(sink)

    def __enter__(self):
        self._stdout.__enter__()
        self._stderr.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self._stderr.__exit__(exc_type, exc, traceback)
        self._stdout.__exit__(exc_type, exc, traceback)
