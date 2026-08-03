"""Central startup validation and secret-safe configuration diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
from urllib.parse import urlsplit

from .data_paths import ApplicationDataPaths
from .settings import (
    ApplicationSettings,
    DataCollectionSettings,
    ServerSettings,
)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
_PLACEHOLDER_SECRETS = frozenset(
    {
        "change-me",
        "changeme",
        "your_api_key_here",
        "your_openai_key_here",
        "your_token_here",
    }
)
_SENSITIVE_NAME_PARTS = ("KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")


class ConfigurationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    severity: ConfigurationSeverity
    code: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class StartupOptions:
    simulation: bool
    websocket_enabled: bool
    websocket_host: str
    websocket_port: int
    log_level: str


@dataclass(frozen=True, slots=True)
class ConfigurationReport:
    issues: tuple[ConfigurationIssue, ...]

    @property
    def errors(self) -> tuple[ConfigurationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is ConfigurationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ConfigurationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is ConfigurationSeverity.WARNING
        )

    def raise_for_errors(self) -> None:
        if self.errors:
            raise StartupConfigurationError(self.errors)


class StartupConfigurationError(ValueError):
    def __init__(self, issues: tuple[ConfigurationIssue, ...]) -> None:
        self.issues = issues
        summary = "; ".join(f"{issue.field}: {issue.message}" for issue in issues)
        super().__init__(f"启动配置校验失败: {summary}")


def validate_startup_configuration(
    settings: ApplicationSettings,
    options: StartupOptions,
) -> ConfigurationReport:
    issues: list[ConfigurationIssue] = []

    log_level = options.log_level.strip().upper()
    if log_level not in _LOG_LEVELS:
        _error(
            issues,
            "invalid_log_level",
            "LOG_LEVEL",
            f"必须是 {', '.join(sorted(_LOG_LEVELS))} 之一",
        )
    if not settings.logging.directory.strip():
        _error(
            issues,
            "invalid_log_directory",
            "LOG_DIRECTORY",
            "不能为空",
        )
    _positive_value(
        settings.logging.retention_days,
        issues,
        "LOG_RETENTION_DAYS",
    )

    _validate_data_paths(settings, issues)
    _validate_data_collection(settings.data_collection, issues)
    _positive_value(
        settings.execution.execution_action_timeout_seconds,
        issues,
        "EXECUTION_ACTION_TIMEOUT_SECONDS",
    )
    _positive_value(
        settings.execution.safety_stop_wait_timeout_seconds,
        issues,
        "SAFETY_STOP_WAIT_TIMEOUT_SECONDS",
    )
    _positive_value(
        settings.voice.voice_speech_startup_wait_timeout_s,
        issues,
        "VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S",
    )
    _non_negative_value(
        settings.execution.execution_arm_move_retry_delay_seconds,
        issues,
        "EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS",
    )
    _non_negative_value(
        settings.execution.execution_gripper_retry_delay_seconds,
        issues,
        "EXECUTION_GRIPPER_RETRY_DELAY_SECONDS",
    )

    if options.websocket_enabled:
        _validate_websocket(settings.server, settings.secrets.websocket_auth_token, options, issues)
    if not options.simulation:
        for field, value in (
            ("ROBOT1_PORT", settings.robot.robot1_port),
            ("ROBOT2_PORT", settings.robot.robot2_port),
            ("MOVE_CONTROLLER_PORT", settings.robot.move_controller_port),
            ("VISION_CAMERA_PORT", settings.vision.vision_camera_port),
            ("MINICPM_GATEWAY_PORT", settings.llm.minicpm_gateway_port),
        ):
            _port_value(value, issues, field)

    provider = settings.llm.llm_default_provider.strip().lower()
    api_keys = {
        "openai": settings.secrets.openai_api_key,
        "deepseek": (settings.secrets.deepseek_api_key or settings.secrets.openai_api_key),
        "dashscope": (settings.secrets.dashscope_api_key or settings.secrets.openai_api_key),
    }
    api_key = api_keys.get(provider, "").strip()
    if provider in {"openai", "deepseek", "dashscope"}:
        if not api_key:
            _warning(
                issues,
                "llm_key_missing",
                "OPENAI_API_KEY",
                "当前 LLM provider 未配置凭据，AI 能力将不可用",
            )
        elif is_placeholder_secret(api_key):
            _error(
                issues,
                "placeholder_secret",
                "OPENAI_API_KEY",
                "不能使用示例占位凭据",
            )
    if is_placeholder_secret(settings.secrets.vveai_api_key):
        _error(
            issues,
            "placeholder_secret",
            "VVEAI_API_KEY",
            "不能使用示例占位凭据",
        )
    _non_negative_value(
        settings.vision.balance_camera_index,
        issues,
        "BALANCE_CAMERA_INDEX",
    )
    _positive_value(
        settings.vision.balance_request_timeout_seconds,
        issues,
        "BALANCE_REQUEST_TIMEOUT_SECONDS",
    )

    return ConfigurationReport(tuple(issues))


def is_loopback_host(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK_HOSTS


def is_sensitive_config_name(name: str) -> bool:
    normalized = name.upper()
    return any(part in normalized for part in _SENSITIVE_NAME_PARTS)


def redact_config_mapping(
    values: Mapping[str, object],
) -> dict[str, object]:
    return {
        name: "<redacted>" if is_sensitive_config_name(name) else value
        for name, value in values.items()
    }


def is_placeholder_secret(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDER_SECRETS


def _validate_data_paths(
    settings: ApplicationSettings,
    issues: list[ConfigurationIssue],
) -> None:
    try:
        paths = ApplicationDataPaths.from_settings(settings.data)
    except (OSError, ValueError):
        _error(
            issues,
            "invalid_data_path",
            "ROBOT_DATA_DIR",
            "数据目录路径无效",
        )
        return

    files = {
        "ACTIONS_LIBRARY_PATH": paths.actions_file,
        "SKILL_LIBRARY_PATH": paths.skills_file,
    }
    if len(set(files.values())) != len(files):
        _error(
            issues,
            "colliding_data_paths",
            "ROBOT_DATA_DIR",
            "动作库和技能库不能指向同一文件",
        )
    if paths.tasks_directory in files.values():
        _error(
            issues,
            "colliding_data_paths",
            "TASKS_DIRECTORY",
            "任务目录不能与数据文件使用同一路径",
        )
    for field, path in files.items():
        _reject_existing_directory(path, field, issues)
    if paths.tasks_directory.exists() and not paths.tasks_directory.is_dir():
        _error(
            issues,
            "data_directory_is_file",
            "TASKS_DIRECTORY",
            "任务目录路径已被普通文件占用",
        )


def _validate_data_collection(
    settings: DataCollectionSettings,
    issues: list[ConfigurationIssue],
) -> None:
    if not 1 <= settings.fps <= 240:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_FPS",
            "必须在 1..240 范围内",
        )
    _non_negative_value(
        settings.camera_index,
        issues,
        "DATA_COLLECTION_CAMERA_INDEX",
    )
    if (
        not settings.arm_ids
        or len(settings.arm_ids) != len(set(settings.arm_ids))
        or any(arm_id not in {"left", "right"} for arm_id in settings.arm_ids)
    ):
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_ARMS",
            "必须包含不重复的 left 或 right",
        )
    supported_formats = {"portable_simplified", "rlbench_native"}
    if settings.format_variant not in supported_formats:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_FORMAT_VARIANT",
            "必须是 portable_simplified 或 rlbench_native",
        )
    elif settings.format_variant == "rlbench_native" and len(settings.arm_ids) != 1:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_ARMS",
            "rlbench_native 必须且只能配置一条机械臂",
        )
    if not settings.save_path.strip():
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_SAVE_PATH",
            "不能为空",
        )
    _non_negative_value(
        settings.minimum_free_bytes,
        issues,
        "DATA_COLLECTION_MIN_FREE_BYTES",
    )
    if not math.isfinite(settings.storage_overhead_factor) or settings.storage_overhead_factor < 1:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR",
            "必须是大于等于 1 的有限数",
        )
    for field, value in (
        (
            "DATA_COLLECTION_STALE_WRITE_SECONDS",
            settings.stale_write_seconds,
        ),
        (
            "DATA_COLLECTION_STOP_TIMEOUT_SECONDS",
            settings.recording_stop_timeout_seconds,
        ),
        (
            "DATA_COLLECTION_MAX_SYNC_SKEW_MS",
            settings.maximum_sync_skew_ms,
        ),
    ):
        _positive_value(value, issues, field)
    has_extrinsics = bool(settings.camera_extrinsics)
    has_metadata = bool(
        settings.camera_extrinsics_reference_frame.strip() or settings.calibration_id.strip()
    )
    if has_extrinsics and len(settings.camera_extrinsics) != 16:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_CAMERA_EXTRINSICS",
            "必须包含 16 个数值",
        )
    if has_extrinsics and (
        not settings.camera_extrinsics_reference_frame.strip()
        or not settings.calibration_id.strip()
    ):
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_CAMERA_EXTRINSICS",
            "配置外参时必须同时配置参考系和标定 ID",
        )
    elif not has_extrinsics and has_metadata:
        _error(
            issues,
            "invalid_data_collection",
            "DATA_COLLECTION_CAMERA_EXTRINSICS",
            "标定元数据不能脱离外参单独配置",
        )


def _reject_existing_directory(
    path: Path,
    field: str,
    issues: list[ConfigurationIssue],
) -> None:
    if path.exists() and path.is_dir():
        _error(
            issues,
            "data_file_is_directory",
            field,
            "数据文件路径已被目录占用",
        )


def _validate_websocket(
    settings: ServerSettings,
    auth_token: str,
    options: StartupOptions,
    issues: list[ConfigurationIssue],
) -> None:
    if not options.websocket_host.strip():
        _error(
            issues,
            "empty_websocket_host",
            "WEBSOCKET_HOST",
            "启用 WebSocket 时监听地址不能为空",
        )
    _port_value(
        options.websocket_port,
        issues,
        "WEBSOCKET_PORT",
    )
    for field, value in (
        ("WEBSOCKET_CONTROL_LEASE_SECONDS", settings.websocket_control_lease_seconds),
        ("WEBSOCKET_SEND_TIMEOUT_SECONDS", settings.websocket_send_timeout_seconds),
        (
            "WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS",
            settings.websocket_slow_send_threshold_seconds,
        ),
        (
            "TELEOPERATION_COMMAND_TIMEOUT_SECONDS",
            settings.teleoperation_command_timeout_seconds,
        ),
        (
            "AUXILIARY_SERVICE_START_TIMEOUT_SECONDS",
            settings.auxiliary_service_start_timeout_seconds,
        ),
        (
            "AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS",
            settings.auxiliary_service_stop_timeout_seconds,
        ),
    ):
        _positive_value(value, issues, field)
    if settings.websocket_slow_send_threshold_seconds > settings.websocket_send_timeout_seconds:
        _error(
            issues,
            "invalid_websocket_slow_send_threshold",
            "WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS",
            "慢发送阈值不能大于发送超时",
        )
    for field, value in (
        ("WEBSOCKET_MAX_MESSAGE_SIZE_BYTES", settings.websocket_max_message_size_bytes),
        (
            "WEBSOCKET_MAX_REQUESTS_PER_SECOND",
            settings.websocket_max_requests_per_second,
        ),
        (
            "WEBSOCKET_MAX_CONCURRENT_REQUESTS",
            settings.websocket_max_concurrent_requests,
        ),
        ("WEBSOCKET_MAX_QUEUED_MESSAGES", settings.websocket_max_queued_messages),
    ):
        _positive_value(value, issues, field)

    auth_token = auth_token.strip()
    if auth_token and is_placeholder_secret(auth_token):
        _error(
            issues,
            "placeholder_secret",
            "WEBSOCKET_AUTH_TOKEN",
            "不能使用示例占位凭据",
        )
    _validate_websocket_transport_security(
        settings,
        auth_token,
        options,
        issues,
    )


def _validate_websocket_transport_security(
    settings: ServerSettings,
    auth_token: str,
    options: StartupOptions,
    issues: list[ConfigurationIssue],
) -> None:
    certificate_path = settings.websocket_tls_certificate_path.strip()
    private_key_path = settings.websocket_tls_private_key_path.strip()
    tls_enabled = bool(certificate_path and private_key_path)
    if bool(certificate_path) != bool(private_key_path):
        _error(
            issues,
            "incomplete_websocket_tls",
            "WEBSOCKET_TLS_CERTIFICATE_PATH",
            "TLS 证书和私钥必须同时配置",
        )
    if certificate_path:
        _require_regular_file(
            certificate_path,
            "WEBSOCKET_TLS_CERTIFICATE_PATH",
            issues,
        )
    if private_key_path:
        _require_regular_file(
            private_key_path,
            "WEBSOCKET_TLS_PRIVATE_KEY_PATH",
            issues,
        )

    for origin in settings.websocket_allowed_origins:
        if not _is_valid_origin(origin):
            _error(
                issues,
                "invalid_websocket_origin",
                "WEBSOCKET_ALLOWED_ORIGINS",
                f"Origin 必须是无路径的 http(s) 源: {origin}",
            )

    loopback = is_loopback_host(options.websocket_host)
    reverse_proxy = settings.websocket_reverse_proxy_mode
    if reverse_proxy and not loopback:
        _error(
            issues,
            "untrusted_reverse_proxy_binding",
            "WEBSOCKET_HOST",
            "反向代理模式必须绑定 loopback，由同机可信代理访问",
        )
    if reverse_proxy and tls_enabled:
        _error(
            issues,
            "ambiguous_websocket_tls_termination",
            "WEBSOCKET_REVERSE_PROXY_MODE",
            "反向代理模式由代理终止 TLS，后端不要同时配置服务端 TLS",
        )
    if not loopback and not tls_enabled:
        _error(
            issues,
            "websocket_tls_required",
            "WEBSOCKET_TLS_CERTIFICATE_PATH",
            "非本机直连必须配置服务端 TLS",
        )

    externally_exposed = not loopback or reverse_proxy or tls_enabled
    if externally_exposed and not auth_token:
        _error(
            issues,
            "websocket_auth_required",
            "WEBSOCKET_AUTH_TOKEN",
            "远程或代理部署必须配置认证密钥",
        )
    if externally_exposed and not settings.websocket_allowed_origins:
        _error(
            issues,
            "websocket_origins_required",
            "WEBSOCKET_ALLOWED_ORIGINS",
            "远程或代理部署必须配置明确的 Origin 白名单",
        )


def _require_regular_file(
    raw_path: str,
    field: str,
    issues: list[ConfigurationIssue],
) -> None:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        _error(
            issues,
            "missing_websocket_tls_file",
            field,
            "配置的 TLS 文件不存在或不是普通文件",
        )


def _is_valid_origin(origin: str) -> bool:
    parsed = urlsplit(origin.strip())
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _port_value(
    value: object,
    issues: list[ConfigurationIssue],
    field: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _error(issues, "invalid_port", field, "必须是 1..65535 的整数")
        return
    if not 1 <= value <= 65_535:
        _error(issues, "invalid_port", field, "必须在 1..65535 范围内")


def _positive_value(
    value: object,
    issues: list[ConfigurationIssue],
    field: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(issues, "invalid_number", field, "必须是正数")
        return
    if not math.isfinite(value) or value <= 0:
        _error(issues, "invalid_number", field, "必须大于 0")


def _non_negative_value(
    value: object,
    issues: list[ConfigurationIssue],
    field: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(issues, "invalid_number", field, "必须是非负数")
        return
    if not math.isfinite(value) or value < 0:
        _error(issues, "invalid_number", field, "不能小于 0")


def _error(
    issues: list[ConfigurationIssue],
    code: str,
    field: str,
    message: str,
) -> None:
    issues.append(
        ConfigurationIssue(
            ConfigurationSeverity.ERROR,
            code,
            field,
            message,
        )
    )


def _warning(
    issues: list[ConfigurationIssue],
    code: str,
    field: str,
    message: str,
) -> None:
    issues.append(
        ConfigurationIssue(
            ConfigurationSeverity.WARNING,
            code,
            field,
            message,
        )
    )
