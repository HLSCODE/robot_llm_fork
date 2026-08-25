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
    LLMProviderCatalogSettings,
    ModelRoutingSettings,
    ServerSettings,
)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
_GUI_THEMES = frozenset({"system", "light", "dark"})
_LLM_PROVIDER_KINDS = frozenset({"openai_compatible", "minicpm_realtime"})
_LLM_CREDENTIAL_ENV_NAMES = frozenset(
    {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"}
)
_RESPONSE_OUTPUT_MODES = frozenset({"text", "native_audio", "text_then_tts"})
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
    if settings.gui.theme not in _GUI_THEMES:
        _error(
            issues,
            "invalid_gui_theme",
            "GUI_THEME",
            f"必须是 {', '.join(sorted(_GUI_THEMES))} 之一",
        )
    for field, value in (
        ("COMMAND_ARM_RELATIVE_STEP_MM", settings.runtime.command_arm_relative_step_mm),
        ("COMMAND_ARM_RELATIVE_MAX_MM", settings.runtime.command_arm_relative_max_mm),
        ("COMMAND_BASE_RELATIVE_STEP_CM", settings.runtime.command_base_relative_step_cm),
        ("COMMAND_BASE_RELATIVE_MAX_CM", settings.runtime.command_base_relative_max_cm),
    ):
        _positive_value(value, issues, field)
    if (
        settings.runtime.command_arm_relative_step_mm
        > settings.runtime.command_arm_relative_max_mm
    ):
        _error(
            issues,
            "command_arm_step_exceeds_maximum",
            "COMMAND_ARM_RELATIVE_STEP_MM",
            "默认步长不得超过单次移动上限",
        )
    if settings.runtime.command_arm_relative_max_mm > 100:
        _error(
            issues,
            "command_arm_max_exceeds_safety_limit",
            "COMMAND_ARM_RELATIVE_MAX_MM",
            "机械臂单次相对移动上限不得超过 100 毫米",
        )
    if (
        settings.runtime.command_base_relative_step_cm
        > settings.runtime.command_base_relative_max_cm
    ):
        _error(
            issues,
            "command_base_step_exceeds_maximum",
            "COMMAND_BASE_RELATIVE_STEP_CM",
            "默认步长不得超过单次移动上限",
        )
    if settings.runtime.command_base_relative_max_cm > 1000:
        _error(
            issues,
            "command_base_max_exceeds_schema_limit",
            "COMMAND_BASE_RELATIVE_MAX_CM",
            "底盘单次相对移动上限不得超过 1000 厘米",
        )

    _validate_data_paths(settings, issues)
    _validate_data_collection(settings.data_collection, issues)
    _validate_llm_configuration(settings, issues)
    if not settings.localization.external_localization_host.strip():
        _error(
            issues,
            "invalid_external_localization_host",
            "EXTERNAL_LOCALIZATION_HOST",
            "不能为空",
        )
    for field, value in (
        (
            "EXTERNAL_LOCALIZATION_RECEIVE_SIZE_BYTES",
            settings.localization.external_localization_receive_size_bytes,
        ),
        (
            "EXTERNAL_LOCALIZATION_SOCKET_TIMEOUT_SECONDS",
            settings.localization.external_localization_socket_timeout_seconds,
        ),
        (
            "EXTERNAL_LOCALIZATION_JOIN_TIMEOUT_SECONDS",
            settings.localization.external_localization_join_timeout_seconds,
        ),
    ):
        _positive_value(value, issues, field)
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
            (
                "EXTERNAL_LOCALIZATION_PORT",
                settings.localization.external_localization_port,
            ),
        ):
            _port_value(value, issues, field)

    api_keys = {
        provider.normalized_id: settings.secrets.credential(provider.credential_env)
        for provider in settings.llm_providers.entries(enabled_only=True)
        if provider.normalized_kind == "openai_compatible"
    }
    configured_providers = {
        settings.llm.default_provider.strip().lower(),
        *(item.strip().lower() for item in settings.llm.fallback_providers),
    }
    for _, route in settings.model_routing.entries():
        configured_providers.update(
            {
                route.provider.strip().lower(),
                route.speech_provider.strip().lower(),
                *(item.strip().lower() for item in route.fallback_providers),
                *(
                    item.strip().lower()
                    for item in route.speech_fallback_providers
                ),
            }
        )
    for provider in sorted(configured_providers & api_keys.keys()):
        api_key = api_keys[provider].strip()
        secret_field = settings.llm_providers.require(provider).credential_env
        if not api_key:
            _warning(
                issues,
                "llm_key_missing",
                secret_field,
                f"路由引用的 {provider} provider 未配置凭据，相关 AI 能力将不可用",
            )
        elif is_placeholder_secret(api_key):
            _error(
                issues,
                "placeholder_secret",
                secret_field,
                "不能使用示例占位凭据",
            )
    _positive_value(
        settings.vision.balance_camera_wait_timeout_seconds,
        issues,
        "BALANCE_CAMERA_WAIT_TIMEOUT_SECONDS",
    )
    _positive_value(
        settings.vision.vision_schema_version,
        issues,
        "VISION_SCHEMA_VERSION",
    )
    _positive_value(
        settings.vision.vision_debug_retention_days,
        issues,
        "VISION_DEBUG_RETENTION_DAYS",
    )
    _positive_value(
        settings.vision.vision_debug_max_runs,
        issues,
        "VISION_DEBUG_MAX_RUNS",
    )
    for version_field, version_value in (
        ("VISION_MODEL_VERSION", settings.vision.vision_model_version),
        ("VISION_CALIBRATION_VERSION", settings.vision.vision_calibration_version),
    ):
        if not version_value.strip():
            _error(
                issues,
                "invalid_vision_version",
                version_field,
                "不能为空",
            )

    return ConfigurationReport(tuple(issues))


def _validate_llm_configuration(
    settings: ApplicationSettings,
    issues: list[ConfigurationIssue],
) -> None:
    catalog = settings.llm_providers
    provider_ids = tuple(provider.normalized_id for provider in catalog.providers)
    if any(not provider_id for provider_id in provider_ids):
        _error(issues, "invalid_llm_provider_id", "llm_providers", "实例 ID 不能为空")
    if len(provider_ids) != len(set(provider_ids)):
        _error(issues, "duplicate_llm_provider_id", "llm_providers", "实例 ID 不能重复")

    for provider in catalog.providers:
        field_prefix = f"llm_providers.{provider.id or '<empty>'}"
        if provider.normalized_kind not in _LLM_PROVIDER_KINDS:
            _error(
                issues,
                "invalid_llm_provider_kind",
                f"{field_prefix}.kind",
                "必须引用已实现的 Provider 适配器",
            )
            continue
        if not provider.model.strip():
            _error(issues, "empty_llm_model", f"{field_prefix}.model", "不能为空")
        modes = tuple(mode.strip().lower() for mode in provider.output_modes)
        if not modes or any(mode not in {"text", "native_audio"} for mode in modes):
            _error(
                issues,
                "invalid_llm_provider_output_modes",
                f"{field_prefix}.output_modes",
                "只能包含 text 或 native_audio",
            )
        if provider.normalized_kind == "openai_compatible":
            credential_env = provider.credential_env.strip().upper()
            if credential_env not in _LLM_CREDENTIAL_ENV_NAMES:
                _error(
                    issues,
                    "invalid_llm_credential_reference",
                    f"{field_prefix}.credential_env",
                    "必须引用受支持的 API Key 环境变量",
                )
        elif provider.enabled:
            if not provider.gateway_host.strip():
                _error(
                    issues,
                    "empty_llm_gateway_host",
                    f"{field_prefix}.gateway_host",
                    "不能为空",
                )
            _port_value(provider.gateway_port, issues, f"{field_prefix}.gateway_port")

    enabled = frozenset(catalog.enabled_ids)
    default_provider = settings.llm.default_provider.strip().lower()
    if default_provider not in enabled:
        _error(
            issues,
            "invalid_default_llm_provider",
            "llm.default_provider",
            "必须引用已启用的 Provider 实例",
        )
    _validate_provider_fallbacks(
        default_provider,
        settings.llm.fallback_providers,
        "llm.fallback_providers",
        issues,
        allowed=enabled,
    )
    _positive_value(settings.llm.request_timeout_s, issues, "llm.request_timeout_s")
    _positive_value(
        settings.llm.circuit_failure_threshold,
        issues,
        "llm.circuit_failure_threshold",
    )
    _positive_value(
        settings.llm.circuit_recovery_seconds,
        issues,
        "llm.circuit_recovery_seconds",
    )
    _validate_model_routing(settings.model_routing, catalog, issues)


def _validate_model_routing(
    settings: ModelRoutingSettings,
    catalog: LLMProviderCatalogSettings,
    issues: list[ConfigurationIssue],
) -> None:
    enabled = frozenset(catalog.enabled_ids)
    native_audio = frozenset(
        provider.normalized_id
        for provider in catalog.entries(enabled_only=True)
        if provider.supports("native_audio")
    )
    for profile_name, route in settings.entries():
        field_prefix = f"model_routing.{profile_name}"
        provider = route.provider.strip().lower()
        if provider not in enabled:
            _error(
                issues,
                "invalid_llm_route_provider",
                f"{field_prefix}.provider",
                "必须引用已注册的 LLM provider",
            )
        _validate_provider_fallbacks(
            provider,
            route.fallback_providers,
            f"{field_prefix}.fallback_providers",
            issues,
            allowed=enabled,
        )

        mode = route.output_mode.strip().lower()
        if mode not in _RESPONSE_OUTPUT_MODES:
            _error(
                issues,
                "invalid_response_output_mode",
                f"{field_prefix}.output_mode",
                "必须是 text、native_audio 或 text_then_tts",
            )
            continue

        speech_provider = route.speech_provider.strip().lower()
        if mode == "text_then_tts":
            if speech_provider not in native_audio:
                _error(
                    issues,
                    "invalid_speech_provider",
                    f"{field_prefix}.speech_provider",
                    "必须引用支持语音输出的 provider",
                )
            _validate_provider_fallbacks(
                speech_provider,
                route.speech_fallback_providers,
                f"{field_prefix}.speech_fallback_providers",
                issues,
                allowed=native_audio,
            )
            continue

        if speech_provider or route.speech_fallback_providers:
            _error(
                issues,
                "unused_speech_route",
                f"{field_prefix}.speech_provider",
                f"{mode} 模式不能配置独立语音 provider",
            )
        if mode == "native_audio" and provider not in native_audio:
            _error(
                issues,
                "provider_lacks_native_audio",
                f"{field_prefix}.provider",
                "native_audio 模式要求 provider 支持原生语音输出",
            )


def _validate_provider_fallbacks(
    provider: str,
    fallbacks: tuple[str, ...],
    field: str,
    issues: list[ConfigurationIssue],
    *,
    allowed: frozenset[str],
) -> None:
    normalized = tuple(item.strip().lower() for item in fallbacks)
    if (
        any(not item or item not in allowed for item in normalized)
        or len(normalized) != len(set(normalized))
        or provider in normalized
    ):
        _error(
            issues,
            "invalid_provider_fallbacks",
            field,
            "必须是不重复的已注册 provider，且不能包含主 provider",
        )


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

    directories = {
        "ACTIONS_LIBRARY_DIRECTORY": paths.actions_directory,
        "WORKFLOWS_DIRECTORY": paths.workflows_directory,
        "WORKFLOW_DRAFTS_DIRECTORY": paths.workflow_drafts_directory,
        "SKILL_LIBRARY_DIRECTORY": paths.skills_directory,
        "TRAJECTORIES_DIRECTORY": paths.trajectories_directory,
    }
    if len(set(directories.values())) != len(directories):
        _error(
            issues,
            "colliding_data_paths",
            "ROBOT_DATA_DIR",
            "动作、工作流、草稿、技能和轨迹目录不能使用同一路径",
        )
    for field, path in directories.items():
        _reject_existing_file(path, field, issues)


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


def _reject_existing_file(
    path: Path,
    field: str,
    issues: list[ConfigurationIssue],
) -> None:
    if path.exists() and not path.is_dir():
        _error(
            issues,
            "data_directory_is_file",
            field,
            "数据目录路径已被普通文件占用",
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

    if not settings.websocket_security_enabled:
        if not is_loopback_host(options.websocket_host):
            _warning(
                issues,
                "websocket_security_disabled",
                "WEBSOCKET_SECURITY_ENABLED",
                "远程 WebSocket 未启用 TLS、认证和 Origin 限制",
            )
        return

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
