"""Deterministic fingerprints for prompts and planning artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .types import LLMContentPart, LLMMessage


def fingerprint_json(value: Any) -> str:
    """Hash a JSON-compatible value using one canonical representation."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def fingerprint_messages(messages: Sequence[LLMMessage]) -> str:
    """Fingerprint exact request messages without retaining their contents."""
    return fingerprint_json([
        {
            "role": message.role,
            "content": _normalize_content(message.content),
        }
        for message in messages
    ])


def _normalize_content(
    content: str | list[LLMContentPart],
) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    return [
        {
            "type": part.type,
            "text": part.text,
            "data": part.data,
            "mime_type": part.mime_type,
            "metadata": _normalize_mapping(part.metadata),
        }
        for part in content
    ]


def _normalize_mapping(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)
