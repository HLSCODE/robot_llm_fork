from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from .json_documents import (
    CollectionDocumentSpec,
    JsonDocumentSchemaError,
    load_collection_document,
    write_collection_document,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATION_DOCUMENT = CollectionDocumentSpec(
    schema="robot-llm.vision-stations",
    collection_key="profiles",
    legacy_kind="list",
)
_PROFILE_VERSION = 1


class VisionConfiguration(Protocol):
    @property
    def model_version(self) -> str: ...

    @property
    def calibration_version(self) -> str: ...


def normalize_arm_name(arm: str | None) -> str:
    text = str(arm or "").strip().lower()
    if text in {"left", "l", "robot1", "r1", "1", "左", "左臂"}:
        return "left"
    if text in {"right", "r", "robot2", "r2", "2", "右", "右臂"}:
        return "right"
    return text or "left"


def arm_display_name(arm: str | None) -> str:
    return "左臂" if normalize_arm_name(arm) == "left" else "右臂"


def profile_key(station_id: str, arm: str | None) -> str:
    return f"{station_id.strip()}::{normalize_arm_name(arm)}"


class VisionStationStorage:
    """Persistent teach profiles keyed by station id and arm."""

    def __init__(
        self,
        stations_file: str | Path,
        *,
        configuration: VisionConfiguration,
    ) -> None:
        path = Path(stations_file)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        self._stations_file = path
        self._configuration = configuration

    @property
    def stations_file(self) -> Path:
        return self._stations_file

    def ensure_directories(self) -> None:
        self._stations_file.parent.mkdir(parents=True, exist_ok=True)

    def load_profiles(self) -> list[dict[str, Any]]:
        path = self._stations_file
        if not path.is_file():
            return []
        loaded = load_collection_document(path, _STATION_DOCUMENT)
        if loaded.requires_migration:
            raise JsonDocumentSchemaError(
                f"{path.name} uses an unversioned legacy vision-station schema"
            )
        profiles: list[dict[str, Any]] = []
        for index, item in enumerate(loaded.collection):
            if not isinstance(item, dict):
                raise JsonDocumentSchemaError(
                    f"vision station profile at index {index} must be an object"
                )
            for field in ("profile_version", "model_version", "calibration_version"):
                if field not in item:
                    raise JsonDocumentSchemaError(
                        f"vision station profile at index {index} is missing {field}"
                    )
            profiles.append(self._normalize_profile(item))
        return profiles

    def save_profiles(self, profiles: list[dict[str, Any]]) -> None:
        self.ensure_directories()
        normalized = [self._normalize_profile(item) for item in profiles]
        write_collection_document(
            self._stations_file,
            _STATION_DOCUMENT,
            normalized,
        )

    def get_profile(
        self,
        station_id: str,
        arm: str | None,
    ) -> dict[str, Any] | None:
        key = profile_key(station_id, arm)
        for profile in self.load_profiles():
            if profile_key(profile.get("station_id", ""), profile.get("arm")) == key:
                return profile
        return None

    def upsert_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not profile.get("station_id"):
            raise ValueError("station_id is required")
        if not profile.get("T_B0_M"):
            raise ValueError("T_B0_M is required")

        now = time.time()
        normalized = self._normalize_profile(profile)
        normalized.setdefault("created_at", now)
        normalized["updated_at"] = now

        profiles = self.load_profiles()
        key = profile_key(normalized["station_id"], normalized["arm"])
        replaced = False
        for index, existing in enumerate(profiles):
            if profile_key(existing.get("station_id", ""), existing.get("arm")) == key:
                normalized["created_at"] = existing.get("created_at", normalized["created_at"])
                profiles[index] = normalized
                replaced = True
                break
        if not replaced:
            profiles.append(normalized)
        self.save_profiles(profiles)
        return normalized

    def list_station_choices(self, arm: str | None = None) -> list[tuple[str, str]]:
        target_arm = normalize_arm_name(arm) if arm else None
        choices: list[tuple[str, str]] = []
        for profile in self.load_profiles():
            profile_arm = normalize_arm_name(profile.get("arm"))
            if target_arm and profile_arm != target_arm:
                continue
            station_id = profile.get("station_id", "")
            if not station_id:
                continue
            station_name = profile.get("station_name") or station_id
            label = f"{station_name} ({arm_display_name(profile_arm)})"
            choices.append((station_id, label))
        return choices

    def _normalize_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        station_id = str(profile.get("station_id") or profile.get("station_name") or "").strip()
        arm = normalize_arm_name(profile.get("arm"))
        station_name = str(profile.get("station_name") or station_id).strip()
        normalized = dict(profile)
        normalized["station_id"] = station_id
        normalized["station_name"] = station_name
        normalized["arm"] = arm
        profile_version = normalized.get("profile_version", _PROFILE_VERSION)
        if profile_version != _PROFILE_VERSION:
            raise JsonDocumentSchemaError(
                f"vision station profile_version {profile_version!r} is unsupported"
            )
        normalized["profile_version"] = _PROFILE_VERSION
        normalized.setdefault("model_version", self._configuration.model_version)
        normalized.setdefault(
            "calibration_version",
            self._configuration.calibration_version,
        )
        if normalized["calibration_version"] != self._configuration.calibration_version:
            raise JsonDocumentSchemaError(
                "vision station calibration_version does not match active configuration"
            )
        if normalized["model_version"] != self._configuration.model_version:
            raise JsonDocumentSchemaError(
                "vision station model_version does not match active configuration"
            )
        normalized.setdefault("camera_name", "")
        normalized.setdefault("photo_pose", [])
        marker = dict(normalized.get("marker") or {})
        if "width" not in marker and normalized.get("marker_width") not in (None, ""):
            marker["width"] = normalized.get("marker_width")
        if "height" not in marker and normalized.get("marker_height") not in (None, ""):
            marker["height"] = normalized.get("marker_height")
        if marker.get("width") not in (None, "") and marker.get("height") not in (None, ""):
            marker["width"] = float(marker["width"])
            marker["height"] = float(marker["height"])
            normalized["marker"] = marker
        return normalized
