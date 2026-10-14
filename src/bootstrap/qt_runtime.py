"""Windows Qt runtime compatibility helpers."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


# Keep the loaded module alive for the lifetime of the process.  Qt resolves
# ``icuuc.dll`` by name, and an Anaconda runtime can otherwise load its older
# ICU build before Qt 6.11 gets a chance to load the Windows ICU component.
_system_icu: ctypes.WinDLL | None = None


def prepare_qt_runtime() -> None:
    """Load Windows' ICU implementation before importing PySide6.

    PySide6's Qt6Core.dll imports the unversioned ICU entry points shipped by
    current Windows builds.  Anaconda adds its own ``Library\\bin`` directory
    to the DLL search path; that copy uses version-suffixed ICU exports and
    causes ``ERROR_PROC_NOT_FOUND`` (0xc0000139) during ``QtCore`` import.
    """

    global _system_icu
    if sys.platform != "win32" or _system_icu is not None:
        return

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    icu_path = system_root / "System32" / "icuuc.dll"
    if not icu_path.is_file():
        return

    try:
        _system_icu = ctypes.WinDLL(str(icu_path))
    except OSError:
        # Let PySide6 report its normal import error on unsupported Windows
        # builds instead of masking it with a startup-specific exception.
        _system_icu = None
