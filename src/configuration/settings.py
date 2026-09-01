"""Immutable validated settings assembled at the process boundary."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypedDict, TypeVar, cast


_SettingsT = TypeVar("_SettingsT")


class TappingConfig(TypedDict):
    port: str
    baudrate: int
    timeout: float
    gripper_address: int
    lift_address: int
    rotation_address: int
    lift_safe_position: int
    lift_dispense_position: int
    rotation_home_position: int
    powder_large_step: int
    powder_medium_step: int
    powder_small_step: int
    powder_micro_step: int
    powder_large_step_threshold_mg: float
    powder_medium_step_threshold_mg: float
    powder_small_step_threshold_mg: float


class PWMServoConfig(TypedDict):
    servo_id: int
    initial_pwm: int
    pwm_min: int
    pwm_max: int
    default_time: int


class PWMNeckConfig(TypedDict):
    port: str
    baudrate: int
    horizontal: PWMServoConfig
    vertical: PWMServoConfig


class CaptureCalibrationConfig(TypedDict):
    rotation_matrix: list[list[float]]
    translation_vector: list[float]
    gripper_offset: list[float]


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    simulation_mode: bool = False
    interaction_turn_timeout_s: float = 90.0
    command_preview_ttl_seconds: float = 120.0
    command_arm_relative_step_mm: float = 10.0
    command_arm_relative_max_mm: float = 100.0
    command_base_relative_step_cm: float = 10.0
    command_base_relative_max_cm: float = 100.0


@dataclass(frozen=True, slots=True)
class GuiSettings:
    theme: str = "system"


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str = "INFO"
    directory: str = "logs"
    retention_days: int = 14


@dataclass(frozen=True, slots=True)
class DataSettings:
    robot_data_dir: str = "data"
    actions_library_directory: str = ""
    workflows_directory: str = ""
    workflow_drafts_directory: str = ""
    skill_library_directory: str = ""
    trajectories_directory: str = ""


@dataclass(frozen=True, slots=True)
class DataCollectionSettings:
    fps: int = 30
    camera_index: int = 0
    arm_ids: tuple[str, ...] = ("left", "right")
    save_path: str = "data/demos"
    format_variant: str = "portable_simplified"
    minimum_free_bytes: int = 1_073_741_824
    storage_overhead_factor: float = 1.25
    stale_write_seconds: float = 3600.0
    random_seed: int = 42
    recording_stop_timeout_seconds: float = 5.0
    maximum_sync_skew_ms: float = 100.0
    camera_extrinsics: tuple[float, ...] = ()
    camera_extrinsics_reference_frame: str = ""
    calibration_id: str = ""


@dataclass(frozen=True, slots=True)
class LocalizationSettings:
    external_localization_host: str = "0.0.0.0"
    external_localization_port: int = 22222
    external_localization_receive_size_bytes: int = 1024
    external_localization_socket_timeout_seconds: float = 0.2
    external_localization_join_timeout_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class ServerSettings:
    websocket_enabled: bool = True
    websocket_security_enabled: bool = False
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 8765
    websocket_control_lease_seconds: float = 30.0
    websocket_max_message_size_bytes: int = 1_048_576
    websocket_max_requests_per_second: int = 120
    websocket_max_concurrent_requests: int = 16
    websocket_max_queued_messages: int = 16
    websocket_send_timeout_seconds: float = 2.0
    websocket_slow_send_threshold_seconds: float = 0.5
    websocket_allowed_origins: tuple[str, ...] = ()
    websocket_tls_certificate_path: str = ""
    websocket_tls_private_key_path: str = ""
    websocket_reverse_proxy_mode: bool = False
    teleoperation_command_timeout_seconds: float = 1.0
    auxiliary_service_start_timeout_seconds: float = 5.0
    auxiliary_service_stop_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class SecretSettings:
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    websocket_auth_token: str = ""

    def credential(self, environment_name: str) -> str:
        """Resolve one supported credential reference without reading the process env."""
        credentials = {
            "OPENAI_API_KEY": self.openai_api_key,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "DASHSCOPE_API_KEY": self.dashscope_api_key,
        }
        return credentials.get(environment_name.strip().upper(), "")


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    execution_action_timeout_seconds: float = 600.0
    execution_arm_move_max_attempts: int = 3
    execution_arm_move_retry_delay_seconds: float = 0.5
    execution_body_poll_interval_seconds: float = 0.1
    execution_gripper_max_attempts: int = 3
    execution_gripper_retry_delay_seconds: float = 0.5
    execution_trajectory_poll_interval_seconds: float = 0.5
    safety_stop_wait_timeout_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """Global inference policy; concrete endpoints live in ``llm_providers``."""

    default_provider: str = "openai"
    default_temperature: float = 0.3
    default_max_tokens: int = 512
    request_timeout_s: float = 60.0
    fallback_providers: tuple[str, ...] = ()
    circuit_failure_threshold: int = 3
    circuit_recovery_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class LLMProviderSettings:
    """One named provider instance and its adapter-specific connection data."""

    id: str
    kind: str
    enabled: bool = True
    model: str = ""
    base_url: str = ""
    credential_env: str = ""
    output_modes: tuple[str, ...] = ("text",)
    gateway_host: str = ""
    gateway_port: int = 0
    ws_scheme: str = "wss"
    gateway_path_prefix: str = ""
    realtime_path: str = "/v1/realtime"

    @property
    def normalized_id(self) -> str:
        return self.id.strip().lower()

    @property
    def normalized_kind(self) -> str:
        return self.kind.strip().lower()

    def supports(self, output_mode: str) -> bool:
        normalized = output_mode.strip().lower()
        return normalized in {item.strip().lower() for item in self.output_modes}


def _default_llm_providers() -> tuple[LLMProviderSettings, ...]:
    return (
        LLMProviderSettings(
            id="openai",
            kind="openai_compatible",
            model="gpt-4o",
            credential_env="OPENAI_API_KEY",
        ),
        LLMProviderSettings(
            id="deepseek",
            kind="openai_compatible",
            model="deepseek-reasoner",
            base_url="https://api.deepseek.com/v1",
            credential_env="DEEPSEEK_API_KEY",
        ),
        LLMProviderSettings(
            id="dashscope",
            kind="openai_compatible",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            credential_env="DASHSCOPE_API_KEY",
        ),
        LLMProviderSettings(
            id="minicpm",
            kind="minicpm_realtime",
            model="minicpm-o",
            output_modes=("text", "native_audio"),
            gateway_host="localhost",
            gateway_port=8006,
        ),
    )


@dataclass(frozen=True, slots=True)
class LLMProviderCatalogSettings:
    """Immutable catalog addressed by provider instance ID."""

    providers: tuple[LLMProviderSettings, ...] = field(
        default_factory=_default_llm_providers
    )

    def entries(self, *, enabled_only: bool = False) -> tuple[LLMProviderSettings, ...]:
        if not enabled_only:
            return self.providers
        return tuple(provider for provider in self.providers if provider.enabled)

    def get(self, provider_id: str) -> LLMProviderSettings | None:
        normalized = provider_id.strip().lower()
        for provider in self.providers:
            if provider.normalized_id == normalized:
                return provider
        return None

    def require(self, provider_id: str) -> LLMProviderSettings:
        provider = self.get(provider_id)
        if provider is None or not provider.enabled:
            raise ValueError(f"未知或未启用的 LLM provider: {provider_id}")
        return provider

    @property
    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(provider.normalized_id for provider in self.entries(enabled_only=True))


@dataclass(frozen=True, slots=True)
class TaskRouteSettings:
    """Deployment policy for one semantic LLM task profile."""

    provider: str
    fallback_providers: tuple[str, ...] = ()
    output_mode: str = "text"
    speech_provider: str = ""
    speech_fallback_providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelRoutingSettings:
    """Inference and speech routes keyed by stable TaskProfile names."""

    instruction_classifier: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(provider="dashscope")
    )
    general_chat: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(
            provider="dashscope",
            output_mode="text_then_tts",
            speech_provider="minicpm",
        )
    )
    robot_command_planner: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(provider="dashscope")
    )
    vision_fusion: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(
            provider="minicpm",
            output_mode="native_audio",
        )
    )
    voice_feedback: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(
            provider="dashscope",
            output_mode="text_then_tts",
            speech_provider="minicpm",
        )
    )
    repeat: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(
            provider="minicpm",
            output_mode="native_audio",
        )
    )
    balance_reading: TaskRouteSettings = field(
        default_factory=lambda: TaskRouteSettings(provider="dashscope")
    )

    def for_profile(self, profile_name: str) -> TaskRouteSettings | None:
        """Return the configured route for one known profile."""
        route = getattr(self, profile_name, None)
        return route if isinstance(route, TaskRouteSettings) else None

    def entries(self) -> tuple[tuple[str, TaskRouteSettings], ...]:
        return tuple(
            (definition.name, getattr(self, definition.name))
            for definition in fields(self)
        )


@dataclass(frozen=True, slots=True)
class RobotSettings:
    """Provider-independent dual-arm selection and motion defaults."""

    provider: str = "realman"
    profile_id: str = ""
    move_velocity: int = 10
    move_radius: int = 0
    move_connect: int = 0
    move_block: int = 1


@dataclass(frozen=True, slots=True)
class RealManRobotSettings:
    """Connection, tool-rack and gripper settings used only by RealMan."""

    model: str = "rm75-dual"
    left_controller_ip: str = "192.168.3.18"
    left_controller_port: int = 8080
    left_initial_pose: tuple[float, ...] = ()
    right_controller_ip: str = "192.168.3.19"
    right_controller_port: int = 8080
    right_initial_pose: tuple[float, ...] = ()
    tool_rack_arm: str = "right"
    tool_rack_slot_1_approach_pose: tuple[float, ...] = ()
    tool_rack_slot_1_attach_pose: tuple[float, ...] = ()
    tool_rack_slot_1_detach_pose: tuple[float, ...] = ()
    tool_rack_slot_1_attach_dwell_seconds: float = 0.5
    tool_rack_slot_1_detach_dwell_seconds: float = 1.0
    tool_rack_slot_2_approach_pose: tuple[float, ...] = ()
    tool_rack_slot_2_attach_pose: tuple[float, ...] = ()
    tool_rack_slot_2_detach_pose: tuple[float, ...] = ()
    tool_rack_slot_2_attach_dwell_seconds: float = 0.5
    tool_rack_slot_2_detach_dwell_seconds: float = 0.5
    max_attempts: int = 5
    gripper_pick_speed: int = 200
    gripper_pick_force: int = 1000
    gripper_pick_timeout: int = 3
    gripper_release_speed: int = 100
    gripper_release_timeout: int = 3


@dataclass(frozen=True, slots=True)
class TianjiRobotSettings:
    """Controller, calibration and limits used only by Tianji robots."""

    model: str = "tianji-dual"
    controller_ip: str = "192.168.1.190"
    subscription_interval_seconds: float = 0.01
    left_base_transform: tuple[tuple[float, ...], ...] = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.1),
        (0.0, -1.0, 0.0, 0.6),
        (0.0, 0.0, 0.0, 1.0),
    )
    right_base_transform: tuple[tuple[float, ...], ...] = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0, -0.1),
        (0.0, 1.0, 0.0, 0.6),
        (0.0, 0.0, 0.0, 1.0),
    )
    left_tool_transform: tuple[tuple[float, ...], ...] = (
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    right_tool_transform: tuple[tuple[float, ...], ...] = (
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    joint_limits_rad: tuple[tuple[float, ...], ...] = (
        (-3.1067, 3.1067),
        (-2.0944, 2.0944),
        (-3.1067, 3.1067),
        (-2.5307, 1.0472),
        (-3.1067, 3.1067),
        (-1.0472, 1.0472),
        (-1.5708, 1.5708),
    )

    def __post_init__(self) -> None:
        if self.subscription_interval_seconds <= 0:
            raise ValueError("subscription_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class MobileBaseSettings:
    """TCP connection settings for the independently registered mobile base."""

    host: str = "192.168.1.216"
    port: int = 12345
    client_bind_port: int | None = None
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def connection_config(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "client_bind_port": self.client_bind_port,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class RobotConfiguration:
    """Complete robot-provider configuration assembled at the process boundary."""

    common: RobotSettings
    realman: RealManRobotSettings
    tianji: TianjiRobotSettings


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    body_serial_port: str = "/dev/ttyUSB1"
    body_baudrate: int = 115200
    body_slave_id: int = 1
    body_timeout: int = 1
    body_di_pan: bool = False
    kuaihuanshou_serial_port: str = "/dev/ttyUSB2"
    kuaihuanshou_baudrate: int = 115200
    kuaihuanshou_timeout: int = 3
    adp_serial_port: str = "/dev/ttyUSB2"
    adp_baudrate: int = 115200
    adp_timeout: int = 5
    adp_max_retries: int = 3
    relay_serial_port: str = "/dev/ttyUSB0"
    relay_baudrate: int = 38400
    relay_timeout: int = 1
    pwm_neck_serial_port: str = "/dev/neck"
    pwm_neck_baudrate: int = 9600
    pwm_neck_h_servo_id: int = 0
    pwm_neck_h_initial_pwm: int = 1600
    pwm_neck_h_pwm_min: int = 1100
    pwm_neck_h_pwm_max: int = 2100
    pwm_neck_h_default_time: int = 1500
    pwm_neck_v_servo_id: int = 1
    pwm_neck_v_initial_pwm: int = 1600
    pwm_neck_v_pwm_min: int = 1200
    pwm_neck_v_pwm_max: int = 1700
    pwm_neck_v_default_time: int = 2500
    expression_display_enabled: bool = False
    expression_display_provider: str = "t5l_dgusii"
    expression_display_config: str = ""
    expression_display_serial_port: str = "COM4"
    expression_display_baudrate: int = 115200
    expression_display_timeout: float = 0.5
    expression_display_write_timeout: float = 1.0
    expression_display_vp_addr: str = "0x5602"
    expression_display_sp_addr: str = "0x8000"
    expression_display_start_value: str = "0x0000"
    expression_display_stop_value: str = "0x0001"
    expression_display_hide_value: str = "0x0002"
    expression_display_clear_before_switch: str = "stop"
    expression_display_switch_delay: float = 0.1
    expression_display_update_icon_range: bool = True
    expression_display_expressions: str = (
        "happy:24:0:63,sad:27:0:63,angry:30:0:63,"
        "speechless:33:0:63,default_1:36:0:63,default_2:39:0:63"
    )
    expression_display_clear_vps: str = ""
    expression_display_test_interval: float = 1.5
    expression_display_tx_delay: float = 0.05
    tapping_serial_port: str = "/dev/ttyACM0"
    tapping_baudrate: int = 115200
    tapping_timeout: float = 0.5
    tapping_gripper_address: int = 9
    tapping_lift_address: int = 7
    tapping_rotation_address: int = 6
    tapping_lift_safe_position: int = 0
    tapping_lift_dispense_position: int = 50000
    tapping_rotation_home_position: int = 0
    powder_dispense_large_step: int = 20000
    powder_dispense_medium_step: int = 8000
    powder_dispense_small_step: int = 2000
    powder_dispense_micro_step: int = 500
    powder_dispense_large_step_threshold_mg: float = 25.0
    powder_dispense_medium_step_threshold_mg: float = 10.0
    powder_dispense_small_step_threshold_mg: float = 3.0

    def body_motor_config(self) -> dict[str, object]:
        return {
            "port": self.body_serial_port,
            "baudrate": self.body_baudrate,
            "slave_id": self.body_slave_id,
            "timeout": self.body_timeout,
        }

    def kuaihuanshou_config(self) -> dict[str, object]:
        return {
            "port": self.kuaihuanshou_serial_port,
            "baudrate": self.kuaihuanshou_baudrate,
            "timeout": self.kuaihuanshou_timeout,
        }

    def adp_config(self) -> dict[str, object]:
        return {
            "port": self.adp_serial_port,
            "baudrate": self.adp_baudrate,
            "timeout": self.adp_timeout,
            "max_retries": self.adp_max_retries,
        }

    def relay_config(self) -> dict[str, object]:
        return {
            "port": self.relay_serial_port,
            "baudrate": self.relay_baudrate,
            "timeout": self.relay_timeout,
        }

    def tapping_config(self) -> TappingConfig:
        return {
            "port": self.tapping_serial_port,
            "baudrate": self.tapping_baudrate,
            "timeout": self.tapping_timeout,
            "gripper_address": self.tapping_gripper_address,
            "lift_address": self.tapping_lift_address,
            "rotation_address": self.tapping_rotation_address,
            "lift_safe_position": self.tapping_lift_safe_position,
            "lift_dispense_position": self.tapping_lift_dispense_position,
            "rotation_home_position": self.tapping_rotation_home_position,
            "powder_large_step": self.powder_dispense_large_step,
            "powder_medium_step": self.powder_dispense_medium_step,
            "powder_small_step": self.powder_dispense_small_step,
            "powder_micro_step": self.powder_dispense_micro_step,
            "powder_large_step_threshold_mg": (
                self.powder_dispense_large_step_threshold_mg
            ),
            "powder_medium_step_threshold_mg": (
                self.powder_dispense_medium_step_threshold_mg
            ),
            "powder_small_step_threshold_mg": (
                self.powder_dispense_small_step_threshold_mg
            ),
        }

    def pwm_neck_config(self) -> PWMNeckConfig:
        return {
            "port": self.pwm_neck_serial_port,
            "baudrate": self.pwm_neck_baudrate,
            "horizontal": {
                "servo_id": self.pwm_neck_h_servo_id,
                "initial_pwm": self.pwm_neck_h_initial_pwm,
                "pwm_min": self.pwm_neck_h_pwm_min,
                "pwm_max": self.pwm_neck_h_pwm_max,
                "default_time": self.pwm_neck_h_default_time,
            },
            "vertical": {
                "servo_id": self.pwm_neck_v_servo_id,
                "initial_pwm": self.pwm_neck_v_initial_pwm,
                "pwm_min": self.pwm_neck_v_pwm_min,
                "pwm_max": self.pwm_neck_v_pwm_max,
                "default_time": self.pwm_neck_v_default_time,
            },
        }

    def expression_display_mapping(self, project_root: Path) -> dict[str, object]:
        config_path = self.expression_display_config
        if config_path:
            path = Path(config_path)
            if not path.is_absolute():
                path = project_root / path
            config_path = str(path)
        return {
            "enabled": self.expression_display_enabled,
            "provider": self.expression_display_provider,
            "config_path": config_path,
            "port": self.expression_display_serial_port,
            "baudrate": self.expression_display_baudrate,
            "timeout": self.expression_display_timeout,
            "write_timeout": self.expression_display_write_timeout,
            "vp_addr": self.expression_display_vp_addr,
            "sp_addr": self.expression_display_sp_addr,
            "start_value": self.expression_display_start_value,
            "stop_value": self.expression_display_stop_value,
            "hide_value": self.expression_display_hide_value,
            "clear_before_switch": self.expression_display_clear_before_switch,
            "switch_delay": self.expression_display_switch_delay,
            "update_icon_range": self.expression_display_update_icon_range,
            "expressions": self.expression_display_expressions,
            "clear_vps": self.expression_display_clear_vps,
            "test_interval": self.expression_display_test_interval,
            "tx_delay": self.expression_display_tx_delay,
        }


class CameraRole(str, Enum):
    """Stable use-case roles assigned to configured camera profiles."""

    VISION_CAPTURE = "vision_capture"
    ROBOT_GRASP = "robot_grasp"
    BALANCE = "balance"
    RELOCALIZATION = "relocalization"


@dataclass(frozen=True, slots=True)
class CameraProfile:
    """One logical camera and its optional capture/relocalization calibration."""

    name: str
    provider: str
    device_id: str
    label: str = ""
    required: bool = False
    roles: tuple[str, ...] = ()
    arms: tuple[str, ...] = ()
    capture_rotation_matrix: tuple[float, ...] = ()
    capture_translation_vector: tuple[float, ...] = ()
    capture_gripper_offset: tuple[float, ...] = ()
    camera_matrix: tuple[float, ...] = ()
    camera_matrix_resolution: tuple[float, ...] = ()
    distortion_coefficients: tuple[float, ...] = ()
    end_effector_to_camera: tuple[tuple[float, ...], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("camera profile name must not be empty")
        if not self.provider.strip():
            raise ValueError(f"camera profile '{self.name}' provider must not be empty")
        if not self.device_id.strip():
            raise ValueError(f"camera profile '{self.name}' device_id must not be empty")

        known_roles = {role.value for role in CameraRole}
        unknown_roles = set(self.roles) - known_roles
        if unknown_roles:
            rendered = ", ".join(sorted(unknown_roles))
            raise ValueError(f"camera profile '{self.name}' has unknown roles: {rendered}")

        if (
            self.provider.strip().lower() == "opencv"
            and CameraRole.ROBOT_GRASP.value in self.roles
        ):
            raise ValueError(
                f"camera profile '{self.name}' uses OpenCV, which does not "
                "provide the depth frames required by the robot_grasp role"
            )

        normalized_arms = {arm.strip().lower() for arm in self.arms}
        unknown_arms = normalized_arms - {"left", "right"}
        if unknown_arms:
            rendered = ", ".join(sorted(unknown_arms))
            raise ValueError(f"camera profile '{self.name}' has unknown arms: {rendered}")
        if len(self.camera_matrix) not in {0, 9}:
            raise ValueError(
                f"camera profile '{self.name}' camera_matrix must contain 9 values"
            )
        if len(self.capture_rotation_matrix) not in {0, 9}:
            raise ValueError(
                f"camera profile '{self.name}' capture_rotation_matrix "
                "must contain 9 values"
            )
        if len(self.capture_translation_vector) not in {0, 3}:
            raise ValueError(
                f"camera profile '{self.name}' capture_translation_vector "
                "must contain 3 values"
            )
        if len(self.capture_gripper_offset) not in {0, 3}:
            raise ValueError(
                f"camera profile '{self.name}' capture_gripper_offset "
                "must contain 3 values"
            )
        if len(self.camera_matrix_resolution) not in {0, 2}:
            raise ValueError(
                f"camera profile '{self.name}' camera_matrix_resolution "
                "must contain width and height"
            )
        if self.end_effector_to_camera and (
            len(self.end_effector_to_camera) != 4
            or any(len(row) != 4 for row in self.end_effector_to_camera)
        ):
            raise ValueError(
                f"camera profile '{self.name}' end_effector_to_camera "
                "must be a 4x4 matrix"
            )
        if CameraRole.ROBOT_GRASP.value in self.roles:
            missing_capture_fields = [
                field_name
                for field_name, value in (
                    ("capture_rotation_matrix", self.capture_rotation_matrix),
                    ("capture_translation_vector", self.capture_translation_vector),
                    ("capture_gripper_offset", self.capture_gripper_offset),
                )
                if not value
            ]
            if missing_capture_fields:
                rendered = ", ".join(missing_capture_fields)
                raise ValueError(
                    f"camera profile '{self.name}' robot_grasp role requires: "
                    f"{rendered}"
                )
        if CameraRole.RELOCALIZATION.value in self.roles:
            missing_relocalization_fields = [
                field_name
                for field_name, value in (
                    ("camera_matrix", self.camera_matrix),
                    ("end_effector_to_camera", self.end_effector_to_camera),
                )
                if not value
            ]
            if missing_relocalization_fields:
                rendered = ", ".join(missing_relocalization_fields)
                raise ValueError(
                    f"camera profile '{self.name}' relocalization role requires: "
                    f"{rendered}"
                )

    @property
    def display_label(self) -> str:
        return self.label.strip() or self.name.strip()

    def supports(self, role: CameraRole, arm: str | None = None) -> bool:
        if role.value not in self.roles:
            return False
        normalized_arm = _normalize_camera_arm(arm)
        if normalized_arm is None or not self.arms:
            return True
        return normalized_arm in {
            configured_arm.strip().lower() for configured_arm in self.arms
        }


@dataclass(frozen=True, slots=True)
class VisionSettings:
    cameras: tuple[CameraProfile, ...] = ()
    realsense_color_width: int = 640
    realsense_color_height: int = 480
    realsense_depth_width: int = 640
    realsense_depth_height: int = 480
    realsense_fps: int = 0
    realsense_jpeg_quality: int = 85
    realsense_align_depth_to_color: bool = True
    camera_encode_fps: int = 5
    camera_probe_timeout_seconds: float = 2.5
    camera_probe_max_attempts: int = 2
    camera_idle_timeout_seconds: float = 10.0
    webcam_width: int = 640
    webcam_height: int = 480
    webcam_fps: int = 30
    webcam_jpeg_quality: int = 85
    vision_camera_host: str = "localhost"
    vision_camera_port: int = 12345
    yolo_model_path: str = "models/best.pt"
    sam_model_path: str = "models/sam2.1_l.pt"
    vision_schema_version: int = 1
    vision_model_version: str = "default-model-v1"
    vision_calibration_version: str = "default-calibration-v1"
    # Relative paths are resolved from the project root by VisionArtifactStore.
    vision_debug_save_dir: str = "data/vision/debug"
    vision_debug_retention_days: int = 7
    vision_debug_max_runs: int = 100
    balance_camera_wait_timeout_seconds: float = 2.0
    vision_default_confidence: float = 0.7
    vision_default_velocity: int = 15
    vision_default_gripper_length: float = 150.0
    vision_default_workflow: str = "bottle"
    vision_prep_offset_x: float = -0.07
    vision_grasp_z: float = -0.24
    vision_bottle_target_offset_x: float = -0.025
    vision_bottle_target_offset_y: float = 0.015
    vision_gmm_components: int = 1
    vision_relocalization_stations_file: str = "data/vision_stations/profiles.json"
    vision_relocalization_default_marker_width: float = 0.158
    vision_relocalization_default_marker_height: float = 0.158
    vision_relocalization_pose_rotation_type: str = "rpy"
    vision_relocalization_pose_angle_unit: str = "rad"
    vision_relocalization_mode: str = "planar"
    vision_relocalization_planar_constraint: str = "none"
    vision_relocalization_save_debug_images: bool = True
    initial_pose: tuple[float, ...] = ()
    left_initial_pose: tuple[float, ...] = ()
    right_initial_pose: tuple[float, ...] = ()
    place_drop_height: float = 0.06
    place_above: tuple[float, ...] = (
        0.0637,
        -0.07351,
        -0.4182,
        3.15,
        0.0,
        1.617,
    )
    place_pos2: tuple[float, ...] = (
        0.285488,
        -0.256408,
        -0.090654,
        3.14,
        0.0,
        1.5,
    )
    place_transfer_pose: tuple[float, ...] = (
        -0.303379,
        0.274441,
        -0.075986,
        -3.081,
        0.137,
        -1.828,
    )
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.camera_probe_timeout_seconds <= 0:
            raise ValueError("camera_probe_timeout_seconds must be positive")
        if self.camera_probe_max_attempts not in {1, 2}:
            raise ValueError("camera_probe_max_attempts must be 1 or 2")
        if self.camera_idle_timeout_seconds < 0:
            raise ValueError("camera_idle_timeout_seconds must not be negative")
        names: set[str] = set()
        devices: set[tuple[str, str]] = set()
        providers: set[str] = set()
        for profile in self.cameras:
            normalized_name = profile.name.strip().casefold()
            if normalized_name in names:
                raise ValueError(f"duplicate camera profile name: {profile.name}")
            names.add(normalized_name)

            provider = profile.provider.strip().lower()
            providers.add(provider)
            device = (provider, profile.device_id.strip().casefold())
            if device in devices:
                raise ValueError(
                    "duplicate camera device_id for provider "
                    f"'{provider}': {profile.device_id}"
                )
            devices.add(device)
        if len(providers) > 1:
            rendered = ", ".join(sorted(providers))
            raise ValueError(
                "the current camera runtime requires one provider per deployment; "
                f"configured providers: {rendered}"
            )

    def camera_provider_name(self) -> str:
        """Return the provider shared by all configured camera profiles."""
        if not self.cameras:
            raise ValueError("camera catalog must contain at least one profile")
        return self.cameras[0].provider.strip().lower()

    def camera_profiles_for_provider(
        self,
        provider: str,
    ) -> tuple[CameraProfile, ...]:
        normalized = provider.strip().lower()
        return tuple(
            profile
            for profile in self.cameras
            if profile.provider.strip().lower() == normalized
        )

    def camera_profile(self, name: str) -> CameraProfile | None:
        normalized = name.strip().casefold()
        if not normalized:
            return None
        for profile in self.cameras:
            if profile.name.strip().casefold() == normalized:
                return profile
        return None

    def camera_for_role(
        self,
        role: CameraRole,
        *,
        arm: str | None = None,
    ) -> CameraProfile | None:
        candidates = tuple(
            profile for profile in self.cameras if role.value in profile.roles
        )
        if not candidates:
            return None
        normalized_arm = _normalize_camera_arm(arm)
        if normalized_arm is not None:
            for profile in candidates:
                if normalized_arm in {
                    configured_arm.strip().lower()
                    for configured_arm in profile.arms
                }:
                    return profile
            for profile in candidates:
                if not profile.arms:
                    return profile
            return None
        return candidates[0]

    def require_camera_for_role(
        self,
        role: CameraRole,
        *,
        arm: str | None = None,
        camera_name: str = "",
    ) -> CameraProfile:
        """Resolve a role assignment without crossing an arm boundary."""
        if camera_name.strip():
            camera = self.camera_profile(camera_name)
            if camera is None:
                raise ValueError(f"unknown camera profile: {camera_name}")
            if not camera.supports(role, arm):
                target = f" for arm '{_normalize_camera_arm(arm)}'" if arm else ""
                raise ValueError(
                    f"camera profile '{camera.name}' is not assigned to "
                    f"role '{role.value}'{target}"
                )
            return camera

        camera = self.camera_for_role(role, arm=arm)
        if camera is None:
            target = f" for arm '{_normalize_camera_arm(arm)}'" if arm else ""
            raise ValueError(
                f"no camera profile is assigned to role '{role.value}'{target}"
            )
        return camera

    def camera_name_for_role(
        self,
        role: CameraRole,
        *,
        arm: str | None = None,
    ) -> str:
        profile = self.camera_for_role(role, arm=arm)
        return profile.name if profile is not None else ""

    def camera_choices(
        self,
        role: CameraRole | None = None,
        *,
        arm: str | None = None,
    ) -> tuple[tuple[str, str], ...]:
        """Return stable ``(name, label)`` choices for presentation layers."""
        profiles = (
            self.cameras
            if role is None
            else tuple(
                profile for profile in self.cameras if profile.supports(role, arm)
            )
        )
        return tuple(
            (profile.name, profile.display_label)
            for profile in profiles
        )

    def capture_calibration_config(
        self,
        *,
        arm: str | None = None,
        camera_name: str = "",
    ) -> CaptureCalibrationConfig:
        camera = self.require_camera_for_role(
            CameraRole.ROBOT_GRASP,
            arm=arm,
            camera_name=camera_name,
        )
        return {
            "rotation_matrix": _matrix3(camera.capture_rotation_matrix),
            "translation_vector": list(camera.capture_translation_vector),
            "gripper_offset": list(camera.capture_gripper_offset),
        }

    def relocalization_config(
        self,
        arm: str | None = None,
        *,
        camera_name: str = "",
    ) -> dict[str, object]:
        camera = self.require_camera_for_role(
            CameraRole.RELOCALIZATION,
            arm=arm,
            camera_name=camera_name,
        )
        missing_calibration = [
            field_name
            for field_name, value in (
                ("camera_matrix", camera.camera_matrix),
                ("end_effector_to_camera", camera.end_effector_to_camera),
            )
            if not value
        ]
        if missing_calibration:
            rendered = ", ".join(missing_calibration)
            raise ValueError(
                f"camera profile '{camera.name}' relocalization role requires: "
                f"{rendered}"
            )
        camera_resolution = camera.camera_matrix_resolution
        return {
            "stations_file": self.vision_relocalization_stations_file,
            "camera_name": camera.name,
            "camera_matrix": _matrix3(camera.camera_matrix),
            "camera_matrix_resolution": (
                list(camera_resolution) if len(camera_resolution) == 2 else None
            ),
            "dist_coeffs": list(
                camera.distortion_coefficients or (0, 0, 0, 0, 0)
            ),
            "marker": {
                "width": self.vision_relocalization_default_marker_width,
                "height": self.vision_relocalization_default_marker_height,
            },
            "pose_rotation_type": self.vision_relocalization_pose_rotation_type,
            "pose_angle_unit": self.vision_relocalization_pose_angle_unit,
            "T_E_C": [list(row) for row in camera.end_effector_to_camera],
            "mode": self.vision_relocalization_mode,
            "planar_constraint": self.vision_relocalization_planar_constraint,
            "save_debug_images": self.vision_relocalization_save_debug_images,
            "configuration": {
                "schema_version": self.vision_schema_version,
                "model_version": self.vision_model_version,
                "calibration_version": self.vision_calibration_version,
            },
        }


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    voice_session_timeout_s: float = 30.0
    voice_session_history_turns: int = 6
    voice_speech_startup_wait_timeout_s: float = 30.0
    voice_tts_enabled: bool = False
    voice_input_enabled: bool = False
    voice_audio_sample_rate: int = 16000
    voice_audio_channels: int = 1
    voice_audio_block_ms: int = 100
    voice_audio_queue_size: int = 300
    voice_audio_latency: str = "high"
    voice_audio_device: str = ""
    voice_audio_show_status: bool = False
    voice_vad_model: str = "fsmn-vad"
    voice_vad_chunk_ms: int = 200
    voice_min_utterance_ms: int = 500
    voice_max_utterance_ms: int = 30000
    voice_end_silence_ms: int = 800
    voice_speech_start_rms_threshold: float = 0.025
    voice_speech_start_confirm_chunks: int = 1
    voice_listening_timeout_s: float = 8.0
    voice_follow_up_listening_timeout_s: float = 25.0
    voice_wake_cooldown_s: float = 1.5
    voice_wake_feedback_enabled: bool = True
    voice_wake_feedback_text: str = "明德博士在，请说。"
    voice_wake_welcome_enabled: bool = False
    voice_wake_welcome_workflow: str = ""
    voice_silence_rms_threshold: float = 0.01
    voice_suppress_model_output: bool = True
    voice_show_asr_timing: bool = False
    voice_asr_model: str = "iic/SenseVoiceSmall"
    voice_asr_punc_model: str = "ct-punc"
    voice_asr_device: str = ""
    voice_asr_batch_size_s: int = 60
    voice_wake_engine: str = "sherpa"
    voice_wake_auto_trigger: bool = False
    voice_kws_encoder: str = ""
    voice_kws_decoder: str = ""
    voice_kws_joiner: str = ""
    voice_kws_tokens: str = ""
    voice_kws_keywords_file: str = "models/kws/keywords.txt"
    voice_kws_provider: str = "cpu"
    voice_kws_threshold: float = 0.35
    voice_kws_score: float = 1.5
    voice_kws_num_threads: int = 1
    voice_kws_max_active_paths: int = 4
    voice_openwakeword_model_paths: str = ""
    voice_openwakeword_threshold: float = 0.6

    def as_runtime_mapping(self) -> dict[str, object]:
        return {
            "session_timeout_s": self.voice_session_timeout_s,
            "session_history_turns": self.voice_session_history_turns,
            "speech_startup_wait_timeout_s": self.voice_speech_startup_wait_timeout_s,
            "tts_enabled": self.voice_tts_enabled,
            "speech_input_enabled": self.voice_input_enabled,
            "wake_word_enabled": self.voice_input_enabled,
            "asr_enabled": self.voice_input_enabled,
            "audio_sample_rate": self.voice_audio_sample_rate,
            "audio_channels": self.voice_audio_channels,
            "audio_block_ms": self.voice_audio_block_ms,
            "audio_queue_size": self.voice_audio_queue_size,
            "audio_latency": self.voice_audio_latency,
            "audio_device": self.voice_audio_device,
            "audio_show_status": self.voice_audio_show_status,
            "vad_model": self.voice_vad_model,
            "vad_chunk_ms": self.voice_vad_chunk_ms,
            "min_utterance_ms": self.voice_min_utterance_ms,
            "max_utterance_ms": self.voice_max_utterance_ms,
            "end_silence_ms": self.voice_end_silence_ms,
            "speech_start_rms_threshold": self.voice_speech_start_rms_threshold,
            "speech_start_confirm_chunks": self.voice_speech_start_confirm_chunks,
            "listening_timeout_s": self.voice_listening_timeout_s,
            "follow_up_listening_timeout_s": self.voice_follow_up_listening_timeout_s,
            "wake_cooldown_s": self.voice_wake_cooldown_s,
            "wake_feedback_enabled": self.voice_wake_feedback_enabled,
            "wake_feedback_text": self.voice_wake_feedback_text,
            "wake_welcome_enabled": self.voice_wake_welcome_enabled,
            "wake_welcome_workflow": self.voice_wake_welcome_workflow,
            "silence_rms_threshold": self.voice_silence_rms_threshold,
            "suppress_model_output": self.voice_suppress_model_output,
            "show_asr_timing": self.voice_show_asr_timing,
            "asr_model": self.voice_asr_model,
            "asr_punc_model": self.voice_asr_punc_model,
            "asr_device": self.voice_asr_device,
            "asr_batch_size_s": self.voice_asr_batch_size_s,
            "wake_engine": self.voice_wake_engine,
            "wake_auto_trigger": self.voice_wake_auto_trigger,
            "kws_encoder": self.voice_kws_encoder,
            "kws_decoder": self.voice_kws_decoder,
            "kws_joiner": self.voice_kws_joiner,
            "kws_tokens": self.voice_kws_tokens,
            "kws_keywords_file": self.voice_kws_keywords_file,
            "kws_provider": self.voice_kws_provider,
            "kws_threshold": self.voice_kws_threshold,
            "kws_score": self.voice_kws_score,
            "kws_num_threads": self.voice_kws_num_threads,
            "kws_max_active_paths": self.voice_kws_max_active_paths,
            "openwakeword_model_paths": self.voice_openwakeword_model_paths,
            "openwakeword_threshold": self.voice_openwakeword_threshold,
        }


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    runtime: RuntimeSettings
    gui: GuiSettings
    logging: LoggingSettings
    data: DataSettings
    data_collection: DataCollectionSettings
    localization: LocalizationSettings
    server: ServerSettings
    secrets: SecretSettings
    execution: ExecutionSettings
    llm: LLMSettings
    llm_providers: LLMProviderCatalogSettings
    model_routing: ModelRoutingSettings
    robot: RobotSettings
    robot_realman: RealManRobotSettings
    robot_tianji: TianjiRobotSettings
    mobile_base: MobileBaseSettings
    devices: DeviceSettings
    vision: VisionSettings
    voice: VoiceSettings

    @classmethod
    def from_config(cls, config: Any) -> ApplicationSettings:
        """Freeze the environment loader output into domain snapshots."""
        return cls(
            runtime=_snapshot(RuntimeSettings, config),
            gui=_snapshot(GuiSettings, config, source_names={"theme": "GUI_THEME"}),
            logging=_snapshot(
                LoggingSettings,
                config,
                source_names={
                    "level": "LOG_LEVEL",
                    "directory": "LOG_DIRECTORY",
                    "retention_days": "LOG_RETENTION_DAYS",
                },
            ),
            data=_snapshot(DataSettings, config),
            data_collection=_snapshot(
                DataCollectionSettings,
                config,
                source_names=_DATA_COLLECTION_SOURCE_NAMES,
            ),
            localization=_snapshot(LocalizationSettings, config),
            server=_snapshot(ServerSettings, config),
            secrets=_snapshot(SecretSettings, config),
            execution=_snapshot(ExecutionSettings, config),
            llm=_snapshot(LLMSettings, config, source_names=_LLM_SOURCE_NAMES),
            llm_providers=_snapshot(LLMProviderCatalogSettings, config),
            model_routing=_snapshot(ModelRoutingSettings, config),
            robot=_snapshot(RobotSettings, config, source_names=_ROBOT_SOURCE_NAMES),
            robot_realman=_snapshot(
                RealManRobotSettings,
                config,
                source_names=_REALMAN_SOURCE_NAMES,
            ),
            robot_tianji=_snapshot(
                TianjiRobotSettings,
                config,
                source_names=_TIANJI_SOURCE_NAMES,
            ),
            mobile_base=_snapshot(
                MobileBaseSettings,
                config,
                source_names=_MOBILE_BASE_SOURCE_NAMES,
            ),
            devices=_snapshot(DeviceSettings, config),
            vision=_snapshot(VisionSettings, config),
            voice=_snapshot(VoiceSettings, config),
        )

    @classmethod
    def defaults(cls) -> ApplicationSettings:
        return cls(
            runtime=RuntimeSettings(),
            gui=GuiSettings(),
            logging=LoggingSettings(),
            data=DataSettings(),
            data_collection=DataCollectionSettings(),
            localization=LocalizationSettings(),
            server=ServerSettings(),
            secrets=SecretSettings(),
            execution=ExecutionSettings(),
            llm=LLMSettings(),
            llm_providers=LLMProviderCatalogSettings(),
            model_routing=ModelRoutingSettings(),
            robot=RobotSettings(),
            robot_realman=RealManRobotSettings(),
            robot_tianji=TianjiRobotSettings(),
            mobile_base=MobileBaseSettings(),
            devices=DeviceSettings(),
            vision=VisionSettings(),
            voice=VoiceSettings(),
        )

    def robot_configuration(self) -> RobotConfiguration:
        """Return the aggregate passed to the selected robot provider."""
        return RobotConfiguration(
            common=self.robot,
            realman=self.robot_realman,
            tianji=self.robot_tianji,
        )

    def robot_profile_id(self) -> str:
        """Return the explicit or provider/model-derived executable-data profile."""
        from .robot_profile import (
            compose_robot_profile_id,
            normalize_robot_profile_id,
        )

        if self.robot.profile_id.strip():
            return normalize_robot_profile_id(self.robot.profile_id)
        provider = self.robot.provider.strip().lower()
        model = (
            self.robot_tianji.model
            if provider == "tianji"
            else self.robot_realman.model
        )
        return compose_robot_profile_id(provider, model)


def _snapshot(
    settings_type: type[_SettingsT],
    config: Any,
    *,
    source_names: dict[str, str] | None = None,
) -> _SettingsT:
    values: dict[str, object] = {}
    for definition in fields(cast(Any, settings_type)):
        source_name = (
            source_names.get(definition.name, definition.name.upper())
            if source_names is not None
            else definition.name.upper()
        )
        if hasattr(config, source_name):
            value = getattr(config, source_name)
        elif definition.default is not MISSING:
            value = definition.default
        else:
            value = cast(Any, definition.default_factory)()
        values[definition.name] = _freeze(value)
    return settings_type(**values)


_DATA_COLLECTION_SOURCE_NAMES = {
    "fps": "DATA_COLLECTION_FPS",
    "camera_index": "DATA_COLLECTION_CAMERA_INDEX",
    "arm_ids": "DATA_COLLECTION_ARMS",
    "save_path": "DATA_COLLECTION_SAVE_PATH",
    "format_variant": "DATA_COLLECTION_FORMAT_VARIANT",
    "minimum_free_bytes": "DATA_COLLECTION_MIN_FREE_BYTES",
    "storage_overhead_factor": "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR",
    "stale_write_seconds": "DATA_COLLECTION_STALE_WRITE_SECONDS",
    "random_seed": "DATA_COLLECTION_RANDOM_SEED",
    "recording_stop_timeout_seconds": ("DATA_COLLECTION_STOP_TIMEOUT_SECONDS"),
    "maximum_sync_skew_ms": "DATA_COLLECTION_MAX_SYNC_SKEW_MS",
    "camera_extrinsics": "DATA_COLLECTION_CAMERA_EXTRINSICS",
    "camera_extrinsics_reference_frame": ("DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME"),
    "calibration_id": "DATA_COLLECTION_CALIBRATION_ID",
}


_LLM_SOURCE_NAMES = {
    "default_provider": "LLM_DEFAULT_PROVIDER",
    "default_temperature": "LLM_DEFAULT_TEMPERATURE",
    "default_max_tokens": "LLM_DEFAULT_MAX_TOKENS",
    "request_timeout_s": "LLM_REQUEST_TIMEOUT_S",
    "fallback_providers": "LLM_FALLBACK_PROVIDERS",
    "circuit_failure_threshold": "LLM_CIRCUIT_FAILURE_THRESHOLD",
    "circuit_recovery_seconds": "LLM_CIRCUIT_RECOVERY_SECONDS",
}

_ROBOT_SOURCE_NAMES = {
    "provider": "ROBOT_PROVIDER",
    "profile_id": "ROBOT_PROFILE_ID",
    "move_velocity": "MOVE_VELOCITY",
    "move_radius": "MOVE_RADIUS",
    "move_connect": "MOVE_CONNECT",
    "move_block": "MOVE_BLOCK",
}

_REALMAN_SOURCE_NAMES = {
    "model": "REALMAN_MODEL",
    "left_controller_ip": "REALMAN_LEFT_CONTROLLER_IP",
    "left_controller_port": "REALMAN_LEFT_CONTROLLER_PORT",
    "left_initial_pose": "REALMAN_LEFT_INITIAL_POSE",
    "right_controller_ip": "REALMAN_RIGHT_CONTROLLER_IP",
    "right_controller_port": "REALMAN_RIGHT_CONTROLLER_PORT",
    "right_initial_pose": "REALMAN_RIGHT_INITIAL_POSE",
}

_TIANJI_SOURCE_NAMES = {
    "model": "TIANJI_MODEL",
    "controller_ip": "TIANJI_CONTROLLER_IP",
    "subscription_interval_seconds": "TIANJI_SUBSCRIPTION_INTERVAL_SECONDS",
}

_MOBILE_BASE_SOURCE_NAMES = {
    "host": "MOBILE_BASE_HOST",
    "port": "MOBILE_BASE_PORT",
    "client_bind_port": "MOBILE_BASE_CLIENT_BIND_PORT",
    "timeout_seconds": "MOBILE_BASE_TIMEOUT_SECONDS",
}


def _freeze(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _normalize_camera_arm(arm: str | None) -> str | None:
    normalized = str(arm or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"left", "l", "robot1", "r1", "1", "左", "左臂"}:
        return "left"
    if normalized in {"right", "r", "robot2", "r2", "2", "右", "右臂"}:
        return "right"
    raise ValueError(f"unknown camera arm: {arm}")


def _matrix3(values: tuple[float, ...]) -> list[list[float]]:
    if len(values) == 9:
        return [
            list(values[0:3]),
            list(values[3:6]),
            list(values[6:9]),
        ]
    return [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
