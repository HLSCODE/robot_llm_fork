from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config_loader import Config


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_STATIONS_FILE = _PROJECT_ROOT / "data" / "vision_stations" / "profiles.json"


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

    @classmethod
    def stations_file(cls) -> Path:
        cfg = Config.get_instance()
        raw_path = getattr(cfg, "VISION_RELOCALIZATION_STATIONS_FILE", "")
        path = Path(raw_path) if raw_path else _DEFAULT_STATIONS_FILE
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @classmethod
    def ensure_directories(cls) -> None:
        cls.stations_file().parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load_profiles(cls) -> list[dict[str, Any]]:
        path = cls.stations_file()
        if not path.is_file():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            profiles = data.get("profiles", [])
        else:
            profiles = data
        return [cls._normalize_profile(item) for item in profiles if isinstance(item, dict)]

    @classmethod
    def save_profiles(cls, profiles: list[dict[str, Any]]) -> None:
        cls.ensure_directories()
        normalized = [cls._normalize_profile(item) for item in profiles]
        payload = {"version": 1, "profiles": normalized}
        with open(cls.stations_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    @classmethod
    def get_profile(cls, station_id: str, arm: str | None) -> dict[str, Any] | None:
        key = profile_key(station_id, arm)
        for profile in cls.load_profiles():
            if profile_key(profile.get("station_id", ""), profile.get("arm")) == key:
                return profile
        return None

    @classmethod
    def upsert_profile(cls, profile: dict[str, Any]) -> dict[str, Any]:
        if not profile.get("station_id"):
            raise ValueError("station_id is required")
        if not profile.get("T_B0_M"):
            raise ValueError("T_B0_M is required")

        now = time.time()
        normalized = cls._normalize_profile(profile)
        normalized.setdefault("created_at", now)
        normalized["updated_at"] = now

        profiles = cls.load_profiles()
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
        cls.save_profiles(profiles)
        return normalized

    @classmethod
    def list_station_choices(cls, arm: str | None = None) -> list[tuple[str, str]]:
        target_arm = normalize_arm_name(arm) if arm else None
        choices: list[tuple[str, str]] = []
        for profile in cls.load_profiles():
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

    @staticmethod
    def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
        station_id = str(profile.get("station_id") or profile.get("station_name") or "").strip()
        arm = normalize_arm_name(profile.get("arm"))
        station_name = str(profile.get("station_name") or station_id).strip()
        normalized = dict(profile)
        normalized["station_id"] = station_id
        normalized["station_name"] = station_name
        normalized["arm"] = arm
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
