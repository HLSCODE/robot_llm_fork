"""Central startup validation and secret-safe configuration diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .data_paths import ApplicationDataPaths


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
    config: Any,
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

    _validate_data_paths(config, issues)
    _positive(
        config,
        issues,
        "EXECUTION_ACTION_TIMEOUT_SECONDS",
        default=600.0,
    )
    _positive(
        config,
        issues,
        "SAFETY_STOP_WAIT_TIMEOUT_SECONDS",
        default=2.0,
    )
    _non_negative(
        config,
        issues,
        "EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS",
        default=0.5,
    )
    _non_negative(
        config,
        issues,
        "EXECUTION_GRIPPER_RETRY_DELAY_SECONDS",
        default=0.5,
    )

    if options.websocket_enabled:
        _validate_websocket(config, options, issues)
    if not options.simulation:
        for field in (
            "ROBOT1_PORT",
            "ROBOT2_PORT",
            "MOVE_CONTROLLER_PORT",
            "VISION_CAMERA_PORT",
            "MINICPM_GATEWAY_PORT",
        ):
            _port(config, issues, field)

    provider = str(getattr(config, "LLM_DEFAULT_PROVIDER", "openai")).strip().lower()
    api_key = str(getattr(config, "OPENAI_API_KEY", "")).strip()
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
    config: Any,
    issues: list[ConfigurationIssue],
) -> None:
    try:
        paths = ApplicationDataPaths.from_config(config)
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
    config: Any,
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
    for field, default in (
        ("WEBSOCKET_CONTROL_LEASE_SECONDS", 30.0),
        ("WEBSOCKET_SEND_TIMEOUT_SECONDS", 2.0),
        ("AUXILIARY_SERVICE_START_TIMEOUT_SECONDS", 5.0),
        ("AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS", 10.0),
    ):
        _positive(config, issues, field, default=default)
    for field, default in (
        ("WEBSOCKET_MAX_MESSAGE_SIZE_BYTES", 1_048_576),
        ("WEBSOCKET_MAX_REQUESTS_PER_SECOND", 120),
        ("WEBSOCKET_MAX_CONCURRENT_REQUESTS", 16),
        ("WEBSOCKET_MAX_QUEUED_MESSAGES", 16),
    ):
        _positive(config, issues, field, default=default)

    auth_token = str(getattr(config, "WEBSOCKET_AUTH_TOKEN", "")).strip()
    if auth_token and is_placeholder_secret(auth_token):
        _error(
            issues,
            "placeholder_secret",
            "WEBSOCKET_AUTH_TOKEN",
            "不能使用示例占位凭据",
        )
    if is_loopback_host(options.websocket_host):
        return
    if auth_token:
        _warning(
            issues,
            "websocket_tls_required",
            "WEBSOCKET_HOST",
            "非本机监听必须通过可信反向代理提供 wss://",
        )
        return
    _warning(
        issues,
        "public_read_only_websocket",
        "WEBSOCKET_AUTH_TOKEN",
        "非本机监听且无凭据时仅开放公共只读接口，业务状态可能被远程读取",
    )


def _port(
    config: Any,
    issues: list[ConfigurationIssue],
    field: str,
) -> None:
    _port_value(getattr(config, field, 0), issues, field)


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


def _positive(
    config: Any,
    issues: list[ConfigurationIssue],
    field: str,
    *,
    default: float,
) -> None:
    value = getattr(config, field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(issues, "invalid_number", field, "必须是正数")
        return
    if value <= 0:
        _error(issues, "invalid_number", field, "必须大于 0")


def _non_negative(
    config: Any,
    issues: list[ConfigurationIssue],
    field: str,
    *,
    default: float,
) -> None:
    value = getattr(config, field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(issues, "invalid_number", field, "必须是非负数")
        return
    if value < 0:
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
