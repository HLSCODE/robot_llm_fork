"""Structured JSONL logging for powder dispense runs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_PATH = _PROJECT_ROOT / "data" / "powder_dispense_logs" / "powder_dispense.jsonl"


def append_powder_dispense_log(record: dict[str, Any], log_path: Path | None = None) -> Path:
    """Append one complete powder dispense run as a JSONL record."""
    path = log_path or DEFAULT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(record)
    payload.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return path
