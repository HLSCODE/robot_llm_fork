"""Configuration-side Robot Profile identity helpers."""

from __future__ import annotations

import re


_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def normalize_robot_profile_id(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or not _PROFILE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "robot profile id must start with a lowercase letter or digit and "
            "contain only lowercase letters, digits, '.', '_' or '-'"
        )
    return normalized


def compose_robot_profile_id(provider: object, model: object) -> str:
    parts = (_slug(provider, "provider"), _slug(model, "model"))
    return normalize_robot_profile_id("-".join(parts))


def _slug(value: object, label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    normalized = normalized.strip("-")
    if not normalized:
        raise ValueError(f"robot {label} must not be empty")
    return normalized
