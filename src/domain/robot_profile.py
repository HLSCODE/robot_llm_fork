"""Stable robot-profile identities used by persisted executable resources."""

from __future__ import annotations

import re


UNSCOPED_ROBOT_PROFILE = "unscoped"
_PROFILE_COMPONENT = re.compile(r"[^a-z0-9]+")


def normalize_robot_profile_id(value: object) -> str:
    """Validate one already-composed profile identifier."""
    normalized = str(value or "").strip().lower()
    if not normalized or normalized in {".", ".."}:
        raise ValueError("robot profile id must not be empty")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", normalized):
        raise ValueError(
            "robot profile id may contain only lowercase letters, digits, '.', '_' and '-'"
        )
    return normalized


def compose_robot_profile_id(provider: object, model: object) -> str:
    """Create a deterministic profile ID from configured provider and model."""
    components = []
    for value, label in ((provider, "provider"), (model, "model")):
        component = _PROFILE_COMPONENT.sub("-", str(value or "").strip().lower()).strip("-")
        if not component:
            raise ValueError(f"robot {label} must not be empty")
        components.append(component)
    return normalize_robot_profile_id("-".join(components))
