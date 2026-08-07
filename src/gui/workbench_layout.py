"""Versioned persistence boundary for user-owned workbench layout preferences."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol

from PySide6.QtCore import QSettings


WORKBENCH_LAYOUT_SCHEMA_VERSION = 2
WORKBENCH_LAYOUT_SETTINGS_KEY = "workbench/layout"


@dataclass(frozen=True, slots=True)
class WorkbenchLayoutState:
    schema_version: int
    side_page: str
    side_visible: bool
    side_width: int
    panel_page: str
    panel_visible: bool


@dataclass(frozen=True, slots=True)
class LayoutLoadResult:
    state: WorkbenchLayoutState | None
    recovered: bool = False
    reason: str | None = None


class WorkbenchLayoutStore(Protocol):
    def load(self) -> LayoutLoadResult: ...

    def save(self, state: WorkbenchLayoutState) -> None: ...

    def clear(self) -> None: ...


class QSettingsWorkbenchLayoutStore:
    """Store one strict JSON document in the platform-native Qt settings backend."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("RobotLLM", "RobotLLM")

    def load(self) -> LayoutLoadResult:
        raw = self._settings.value(WORKBENCH_LAYOUT_SETTINGS_KEY)
        if raw is None:
            return LayoutLoadResult(None)
        if not isinstance(raw, str):
            return self._recover("布局偏好不是 JSON 文本")
        try:
            payload = json.loads(raw)
            state = _decode_layout_state(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return self._recover(str(error))
        return LayoutLoadResult(state)

    def save(self, state: WorkbenchLayoutState) -> None:
        payload = json.dumps(
            asdict(state),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._settings.setValue(WORKBENCH_LAYOUT_SETTINGS_KEY, payload)
        self._settings.sync()

    def clear(self) -> None:
        self._settings.remove(WORKBENCH_LAYOUT_SETTINGS_KEY)
        self._settings.sync()

    def _recover(self, reason: str) -> LayoutLoadResult:
        self.clear()
        return LayoutLoadResult(None, recovered=True, reason=reason)


def _decode_layout_state(payload: object) -> WorkbenchLayoutState:
    if not isinstance(payload, dict):
        raise ValueError("布局偏好根节点必须是对象")
    expected_fields = {
        "schema_version",
        "side_page",
        "side_visible",
        "side_width",
        "panel_page",
        "panel_visible",
    }
    if set(payload) != expected_fields:
        raise ValueError("布局偏好字段不完整或包含未知字段")
    if payload["schema_version"] != WORKBENCH_LAYOUT_SCHEMA_VERSION:
        raise ValueError("布局偏好版本不受支持")
    for field in ("side_page", "panel_page"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise ValueError(f"{field} 必须是非空字符串")
    for field in ("side_visible", "panel_visible"):
        if type(payload[field]) is not bool:
            raise ValueError(f"{field} 必须是布尔值")
    for field in ("side_width",):
        value = payload[field]
        if type(value) is not int or not 1 <= value <= 10_000:
            raise ValueError(f"{field} 超出允许范围")
    return WorkbenchLayoutState(**payload)
