"""Known environment-variable overrides for application settings."""

from __future__ import annotations

from dataclasses import fields
import os
from typing import get_type_hints

from .settings import ApplicationSettings
from .value_parsing import parse_environment_value


_FIELD_ENV_NAMES: dict[tuple[str, str], str] = {
    ("gui", "theme"): "GUI_THEME",
    ("logging", "level"): "LOG_LEVEL",
    ("logging", "directory"): "LOG_DIRECTORY",
    ("logging", "retention_days"): "LOG_RETENTION_DAYS",
    ("data_collection", "fps"): "DATA_COLLECTION_FPS",
    ("data_collection", "camera_index"): "DATA_COLLECTION_CAMERA_INDEX",
    ("data_collection", "arm_ids"): "DATA_COLLECTION_ARMS",
    ("data_collection", "save_path"): "DATA_COLLECTION_SAVE_PATH",
    ("data_collection", "format_variant"): "DATA_COLLECTION_FORMAT_VARIANT",
    ("data_collection", "minimum_free_bytes"): "DATA_COLLECTION_MIN_FREE_BYTES",
    ("data_collection", "storage_overhead_factor"): (
        "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR"
    ),
    ("data_collection", "stale_write_seconds"): "DATA_COLLECTION_STALE_WRITE_SECONDS",
    ("data_collection", "random_seed"): "DATA_COLLECTION_RANDOM_SEED",
    ("data_collection", "recording_stop_timeout_seconds"): (
        "DATA_COLLECTION_STOP_TIMEOUT_SECONDS"
    ),
    ("data_collection", "maximum_sync_skew_ms"): "DATA_COLLECTION_MAX_SYNC_SKEW_MS",
    ("data_collection", "camera_extrinsics"): "DATA_COLLECTION_CAMERA_EXTRINSICS",
    ("data_collection", "camera_extrinsics_reference_frame"): (
        "DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME"
    ),
    ("data_collection", "calibration_id"): "DATA_COLLECTION_CALIBRATION_ID",
    ("llm", "default_provider"): "LLM_DEFAULT_PROVIDER",
    ("llm", "default_temperature"): "LLM_DEFAULT_TEMPERATURE",
    ("llm", "default_max_tokens"): "LLM_DEFAULT_MAX_TOKENS",
    ("llm", "request_timeout_s"): "LLM_REQUEST_TIMEOUT_S",
    ("llm", "fallback_providers"): "LLM_FALLBACK_PROVIDERS",
    ("llm", "circuit_failure_threshold"): "LLM_CIRCUIT_FAILURE_THRESHOLD",
    ("llm", "circuit_recovery_seconds"): "LLM_CIRCUIT_RECOVERY_SECONDS",
    ("robot", "provider"): "ROBOT_PROVIDER",
    ("robot", "move_velocity"): "MOVE_VELOCITY",
    ("robot", "move_radius"): "MOVE_RADIUS",
    ("robot", "move_connect"): "MOVE_CONNECT",
    ("robot", "move_block"): "MOVE_BLOCK",
    ("robot_realman", "model"): "REALMAN_MODEL",
    ("robot_realman", "left_controller_ip"): "REALMAN_LEFT_CONTROLLER_IP",
    ("robot_realman", "left_controller_port"): "REALMAN_LEFT_CONTROLLER_PORT",
    ("robot_realman", "left_initial_pose"): "REALMAN_LEFT_INITIAL_POSE",
    ("robot_realman", "right_controller_ip"): "REALMAN_RIGHT_CONTROLLER_IP",
    ("robot_realman", "right_controller_port"): "REALMAN_RIGHT_CONTROLLER_PORT",
    ("robot_realman", "right_initial_pose"): "REALMAN_RIGHT_INITIAL_POSE",
    ("robot_tianji", "model"): "TIANJI_MODEL",
    ("robot_tianji", "controller_ip"): "TIANJI_CONTROLLER_IP",
    ("robot_tianji", "subscription_interval_seconds"): (
        "TIANJI_SUBSCRIPTION_INTERVAL_SECONDS"
    ),
    ("mobile_base", "host"): "MOBILE_BASE_HOST",
    ("mobile_base", "port"): "MOBILE_BASE_PORT",
    ("mobile_base", "client_bind_port"): "MOBILE_BASE_CLIENT_BIND_PORT",
    ("mobile_base", "timeout_seconds"): "MOBILE_BASE_TIMEOUT_SECONDS",
}


def environment_name(group_name: str, field_name: str) -> str:
    """Return the stable public environment name for a settings field."""
    return _FIELD_ENV_NAMES.get((group_name, field_name), field_name.upper())


def environment_overrides() -> dict[str, dict[str, object]]:
    """Parse only environment variables declared by the settings schema."""
    group_types = get_type_hints(ApplicationSettings)
    overrides: dict[str, dict[str, object]] = {}
    for group_definition in fields(ApplicationSettings):
        group_name = group_definition.name
        settings_type = group_types[group_name]
        field_types = get_type_hints(settings_type)
        group_values: dict[str, object] = {}
        for definition in fields(settings_type):
            env_name = environment_name(group_name, definition.name)
            if env_name not in os.environ:
                continue
            group_values[definition.name] = parse_environment_value(
                os.environ[env_name],
                field_types[definition.name],
                env_name,
            )
        if group_values:
            overrides[group_name] = group_values
    return overrides
