"""Versioned persistence for vision relocalization stations."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from ..domain.arm_names import normalize_arm_name
from .json_documents import (
    CollectionDocumentSpec,
    JsonDocumentSchemaError,
    load_collection_document,
    migrate_collection_document,
    read_json_document,
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
        collection, requires_migration = self._load_collection(path)
        if requires_migration:
            raise JsonDocumentSchemaError(
                f"{path.name} requires data migration; run 'robot-init migrate-data'"
            )
        return self._normalize_collection(collection, require_version_fields=True)

    def migrate_legacy_document(self) -> bool:
        """Upgrade a recognized legacy document during explicit data initialization."""
        path = self._stations_file
        if not path.is_file():
            return False
        collection, requires_migration = self._load_collection(path)
        if not requires_migration:
            return False
        profiles = self._normalize_collection(collection, require_version_fields=False)
        migrate_collection_document(path, _STATION_DOCUMENT, profiles)
        return True

    def _normalize_collection(
        self,
        collection: list[Any],
        *,
        require_version_fields: bool,
    ) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                raise JsonDocumentSchemaError(
                    f"vision station profile at index {index} must be an object"
                )
            if require_version_fields:
                for field in ("profile_version", "model_version", "calibration_version"):
                    if field not in item:
                        raise JsonDocumentSchemaError(
                            f"vision station profile at index {index} is missing {field}"
                        )
            profiles.append(self._normalize_profile(item))
        return profiles

    @staticmethod
    def _load_collection(path: Path) -> tuple[list[Any], bool]:
        try:
            loaded = load_collection_document(path, _STATION_DOCUMENT)
        except JsonDocumentSchemaError as error:
            document = read_json_document(path)
            legacy = _legacy_station_profiles(document, path.name)
            if legacy is None:
                raise error
            return legacy, True
        return loaded.collection, loaded.requires_migration

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


def _legacy_station_profiles(document: Any, filename: str) -> list[Any] | None:
    """Recognize the pre-schema station wrapper without accepting arbitrary JSON."""
    if not isinstance(document, dict) or "schema" in document:
        return None
    if not set(document).issubset({"version", "profiles"}) or "profiles" not in document:
        return None
    version = document.get("version", 1)
    if isinstance(version, bool) or version != 1:
        raise JsonDocumentSchemaError(
            f"{filename} legacy version {version!r} is unsupported; expected 1"
        )
    profiles = document["profiles"]
    if not isinstance(profiles, list):
        raise JsonDocumentSchemaError(
            f"{filename} legacy field 'profiles' must be a JSON array"
        )
    return profiles
