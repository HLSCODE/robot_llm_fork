"""Locate writable application files separately from bundled resources."""

from pathlib import Path
import sys


def is_portable() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_root() -> Path:
    """Portable installs own data beside the executable, never in _internal."""
    if is_portable():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
