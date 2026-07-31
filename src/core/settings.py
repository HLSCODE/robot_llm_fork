"""Immutable domain settings assembled at the process boundary."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar, cast


_SettingsT = TypeVar("_SettingsT")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    log_level: str = "INFO"
    simulation_mode: bool = False
    interaction_turn_timeout_s: float = 90.0
    command_preview_ttl_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class DataSettings:
    robot_data_dir: str = "data"
    actions_library_path: str = ""
    tasks_directory: str = ""
    skill_library_path: str = ""


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
class ServerSettings:
    websocket_enabled: bool = True
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
    minicpm_ask_api_key: str = ""
    websocket_auth_token: str = ""
    vveai_api_key: str = ""


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
    openai_model: str = "gpt-4o"
    openai_base_url: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = ""
    dashscope_model: str = ""
    dashscope_base_url: str = ""
    llm_default_provider: str = "openai"
    llm_default_temperature: float = 0.3
    llm_default_max_tokens: int = 512
    llm_request_timeout_s: float = 60.0
    llm_fallback_providers: tuple[str, ...] = ()
    llm_circuit_failure_threshold: int = 3
    llm_circuit_recovery_seconds: float = 30.0
    minicpm_gateway_host: str = "localhost"
    minicpm_gateway_port: int = 8006
    minicpm_ws_scheme: str = "wss"
    minicpm_gateway_path_prefix: str = ""
    minicpm_realtime_path: str = "/v1/realtime"
    minicpm_model: str = "minicpm-o"
    minicpm_ask_enabled: bool = True
    minicpm_ask_base_url: str = ""
    minicpm_ask_model: str = "gpt-4o-mini"


@dataclass(frozen=True, slots=True)
class RobotSettings:
    robot_provider: str = "realman"
    robot_model: str = "rm75-dual"
    robot1_ip: str = "192.168.3.18"
    robot1_port: int = 8080
    robot1_initial_pose: tuple[float, ...] = ()
    robot2_ip: str = "192.168.3.19"
    robot2_port: int = 8080
    robot2_initial_pose: tuple[float, ...] = ()
    robot_tool_rack_arm: str = "right"
    robot_tool_rack_slot_1_approach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_1_attach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_1_detach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_1_attach_dwell_seconds: float = 0.5
    robot_tool_rack_slot_1_detach_dwell_seconds: float = 1.0
    robot_tool_rack_slot_2_approach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_2_attach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_2_detach_pose: tuple[float, ...] = ()
    robot_tool_rack_slot_2_attach_dwell_seconds: float = 0.5
    robot_tool_rack_slot_2_detach_dwell_seconds: float = 0.5
    move_controller_host: str = "192.168.1.216"
    move_controller_port: int = 12345
    move_controller_client_bind_port: int | None = None
    move_velocity: int = 10
    move_radius: int = 0
    move_connect: int = 0
    move_block: int = 1
    max_attempts: int = 5
    gripper_pick_speed: int = 200
    gripper_pick_force: int = 1000
    gripper_pick_timeout: int = 3
    gripper_release_speed: int = 100
    gripper_release_timeout: int = 3

    def move_controller_config(self) -> dict[str, object]:
        return {
            "host": self.move_controller_host,
            "port": self.move_controller_port,
            "client_bind_port": self.move_controller_client_bind_port,
        }


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

    def tapping_config(self) -> dict[str, object]:
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
        }

    def pwm_neck_config(self) -> dict[str, object]:
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


@dataclass(frozen=True, slots=True)
class VisionSettings:
    camera_provider: str = "auto"
    realsense_device_sn: str = ""
    realsense_device_names: str = ""
    realsense_color_width: int = 640
    realsense_color_height: int = 480
    realsense_depth_width: int = 640
    realsense_depth_height: int = 480
    realsense_fps: int = 0
    realsense_jpeg_quality: int = 85
    realsense_align_depth_to_color: bool = True
    camera_encode_fps: int = 5
    webcam_device_indexes: str = "0"
    webcam_device_names: str = ""
    webcam_width: int = 640
    webcam_height: int = 480
    webcam_fps: int = 30
    webcam_jpeg_quality: int = 85
    vision_camera_host: str = "localhost"
    vision_camera_port: int = 12345
    yolo_model_path: str = "models/best.pt"
    sam_model_path: str = "models/sam2.1_l.pt"
    vision_debug_save_dir: str = "pictures"
    balance_camera_index: int = 12
    balance_request_timeout_seconds: float = 30.0
    vveai_base_url: str = "https://api.vveai.com/v1"
    vveai_model: str = "doubao-seed-1-8-251228"
    vision_rotation_matrix: tuple[float, ...] = (
        0.00215684,
        0.97503835,
        0.22202606,
        -0.99995231,
        -0.0000119,
        0.00976617,
        0.00952503,
        -0.22203654,
        0.97499182,
    )
    vision_translation_vector: tuple[float, ...] = (
        -0.10273135,
        0.03312807,
        -0.07214614,
    )
    vision_gripper_offset: tuple[float, ...] = (3.146, 0.0, 3.128)
    vision_default_confidence: float = 0.7
    vision_default_velocity: int = 15
    vision_default_gripper_length: float = 150.0
    vision_default_workflow: str = "bottle"
    vision_camera_name: str = ""
    vision_prep_offset_x: float = -0.07
    vision_grasp_z: float = -0.24
    vision_bottle_target_offset_x: float = -0.025
    vision_bottle_target_offset_y: float = 0.015
    vision_gmm_components: int = 1
    vision_relocalization_stations_file: str = "data/vision_stations/profiles.json"
    vision_relocalization_left_camera_name: str = ""
    vision_relocalization_right_camera_name: str = ""
    vision_relocalization_left_camera_matrix: tuple[float, ...] = ()
    vision_relocalization_right_camera_matrix: tuple[float, ...] = ()
    vision_relocalization_left_camera_matrix_resolution: tuple[float, ...] = ()
    vision_relocalization_right_camera_matrix_resolution: tuple[float, ...] = ()
    vision_relocalization_left_dist_coeffs: tuple[float, ...] = ()
    vision_relocalization_right_dist_coeffs: tuple[float, ...] = ()
    vision_relocalization_default_marker_width: float = 0.158
    vision_relocalization_default_marker_height: float = 0.158
    vision_relocalization_pose_rotation_type: str = "rpy"
    vision_relocalization_pose_angle_unit: str = "rad"
    vision_relocalization_left_t_e_c: tuple[tuple[float, ...], ...] = ()
    vision_relocalization_right_t_e_c: tuple[tuple[float, ...], ...] = ()
    vision_relocalization_mode: str = "planar"
    vision_relocalization_planar_constraint: str = "none"
    vision_relocalization_save_debug_images: bool = True
    vision_relocalization_debug_dir: str = "data/vision_stations/debug"
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

    def calibration_config(self) -> dict[str, list[float] | list[list[float]]]:
        return {
            "rotation_matrix": _matrix3(self.vision_rotation_matrix),
            "translation_vector": list(self.vision_translation_vector),
            "gripper_offset": list(self.vision_gripper_offset),
        }

    def relocalization_config(self, arm: str | None = None) -> dict[str, object]:
        arm_text = str(arm or "").strip().lower()
        is_right = arm_text in {"right", "r", "robot2", "r2", "2", "右", "右臂"}
        camera_name = (
            self.vision_relocalization_right_camera_name
            if is_right
            else self.vision_relocalization_left_camera_name
        )
        camera_values = (
            self.vision_relocalization_right_camera_matrix
            if is_right
            else self.vision_relocalization_left_camera_matrix
        )
        camera_resolution = (
            self.vision_relocalization_right_camera_matrix_resolution
            if is_right
            else self.vision_relocalization_left_camera_matrix_resolution
        )
        dist_coeffs = (
            self.vision_relocalization_right_dist_coeffs
            if is_right
            else self.vision_relocalization_left_dist_coeffs
        )
        transform = (
            self.vision_relocalization_right_t_e_c
            if is_right
            else self.vision_relocalization_left_t_e_c
        )
        return {
            "stations_file": self.vision_relocalization_stations_file,
            "camera_name": camera_name,
            "camera_matrix": _matrix3(camera_values),
            "camera_matrix_resolution": (
                list(camera_resolution) if len(camera_resolution) == 2 else None
            ),
            "dist_coeffs": list(dist_coeffs or (0, 0, 0, 0, 0)),
            "marker": {
                "width": self.vision_relocalization_default_marker_width,
                "height": self.vision_relocalization_default_marker_height,
            },
            "pose_rotation_type": self.vision_relocalization_pose_rotation_type,
            "pose_angle_unit": self.vision_relocalization_pose_angle_unit,
            "T_E_C": [list(row) for row in transform],
            "mode": self.vision_relocalization_mode,
            "planar_constraint": self.vision_relocalization_planar_constraint,
            "save_debug_images": self.vision_relocalization_save_debug_images,
            "debug_dir": self.vision_relocalization_debug_dir,
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
    voice_wake_welcome_task: str = ""
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
            "wake_welcome_task": self.voice_wake_welcome_task,
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
    data: DataSettings
    data_collection: DataCollectionSettings
    server: ServerSettings
    secrets: SecretSettings
    execution: ExecutionSettings
    llm: LLMSettings
    robot: RobotSettings
    devices: DeviceSettings
    vision: VisionSettings
    voice: VoiceSettings

    @classmethod
    def from_config(cls, config: Any) -> ApplicationSettings:
        """Freeze the environment loader output into domain snapshots."""
        return cls(
            runtime=_snapshot(RuntimeSettings, config),
            data=_snapshot(DataSettings, config),
            data_collection=_snapshot(
                DataCollectionSettings,
                config,
                source_names=_DATA_COLLECTION_SOURCE_NAMES,
            ),
            server=_snapshot(ServerSettings, config),
            secrets=_snapshot(SecretSettings, config),
            execution=_snapshot(ExecutionSettings, config),
            llm=_snapshot(LLMSettings, config),
            robot=_snapshot(RobotSettings, config),
            devices=_snapshot(DeviceSettings, config),
            vision=_snapshot(VisionSettings, config),
            voice=_snapshot(VoiceSettings, config),
        )

    @classmethod
    def defaults(cls) -> ApplicationSettings:
        return cls(
            runtime=RuntimeSettings(),
            data=DataSettings(),
            data_collection=DataCollectionSettings(),
            server=ServerSettings(),
            secrets=SecretSettings(),
            execution=ExecutionSettings(),
            llm=LLMSettings(),
            robot=RobotSettings(),
            devices=DeviceSettings(),
            vision=VisionSettings(),
            voice=VoiceSettings(),
        )


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


def _freeze(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


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
