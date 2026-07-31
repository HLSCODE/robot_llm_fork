"""
配置加载器
统一管理所有配置项，从 config.env 文件加载
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .settings import ApplicationSettings


class ConfigLoadError(ValueError):
    """Configuration could not be parsed into a complete snapshot."""


class _EnvironmentConfig:
    """Mutable environment adapter used only while creating settings snapshots."""

    # 配置项
    # LLM/AI 配置
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_BASE_URL: str = ""
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = ""
    DEEPSEEK_BASE_URL: str = ""
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_MODEL: str = ""
    DASHSCOPE_BASE_URL: str = ""
    LLM_DEFAULT_PROVIDER: str = "openai"
    LLM_DEFAULT_TEMPERATURE: float = 0.3
    LLM_DEFAULT_MAX_TOKENS: int = 512
    LLM_REQUEST_TIMEOUT_S: float = 60.0
    LLM_FALLBACK_PROVIDERS: tuple[str, ...] = ()
    LLM_CIRCUIT_FAILURE_THRESHOLD: int = 3
    LLM_CIRCUIT_RECOVERY_SECONDS: float = 30.0
    INTERACTION_TURN_TIMEOUT_S: float = 90.0
    COMMAND_PREVIEW_TTL_SECONDS: float = 120.0
    VOICE_SESSION_TIMEOUT_S: float = 30.0
    VOICE_SESSION_HISTORY_TURNS: int = 6
    VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S: float = 30.0
    VOICE_TTS_ENABLED: bool = False
    VOICE_INPUT_ENABLED: bool = False
    VOICE_AUDIO_SAMPLE_RATE: int = 16000
    VOICE_AUDIO_CHANNELS: int = 1
    VOICE_AUDIO_BLOCK_MS: int = 100
    VOICE_AUDIO_QUEUE_SIZE: int = 300
    VOICE_AUDIO_LATENCY: str = "high"
    VOICE_AUDIO_DEVICE: str = ""
    VOICE_AUDIO_SHOW_STATUS: bool = False
    VOICE_VAD_MODEL: str = "fsmn-vad"
    VOICE_VAD_CHUNK_MS: int = 200
    VOICE_MIN_UTTERANCE_MS: int = 500
    VOICE_MAX_UTTERANCE_MS: int = 30000
    VOICE_END_SILENCE_MS: int = 800
    VOICE_SPEECH_START_RMS_THRESHOLD: float = 0.025
    VOICE_SPEECH_START_CONFIRM_CHUNKS: int = 1
    VOICE_LISTENING_TIMEOUT_S: float = 8.0
    VOICE_FOLLOW_UP_LISTENING_TIMEOUT_S: float = 25.0
    VOICE_WAKE_COOLDOWN_S: float = 1.5
    VOICE_WAKE_FEEDBACK_ENABLED: bool = True
    VOICE_WAKE_FEEDBACK_TEXT: str = "明德博士在，请说。"
    VOICE_WAKE_WELCOME_ENABLED: bool = False
    VOICE_WAKE_WELCOME_TASK: str = ""
    VOICE_SILENCE_RMS_THRESHOLD: float = 0.01
    VOICE_SUPPRESS_MODEL_OUTPUT: bool = True
    VOICE_SHOW_ASR_TIMING: bool = False
    VOICE_ASR_MODEL: str = "iic/SenseVoiceSmall"
    VOICE_ASR_PUNC_MODEL: str = "ct-punc"
    VOICE_ASR_DEVICE: str = ""
    VOICE_ASR_BATCH_SIZE_S: int = 60
    VOICE_WAKE_ENGINE: str = "sherpa"
    VOICE_WAKE_AUTO_TRIGGER: bool = False
    VOICE_KWS_ENCODER: str = "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/encoder-epoch-13-avg-2-chunk-16-left-64.onnx"
    VOICE_KWS_DECODER: str = "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/decoder-epoch-13-avg-2-chunk-16-left-64.onnx"
    VOICE_KWS_JOINER: str = "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/joiner-epoch-13-avg-2-chunk-16-left-64.onnx"
    VOICE_KWS_TOKENS: str = "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/tokens.txt"
    VOICE_KWS_KEYWORDS_FILE: str = "models/kws/keywords.txt"
    VOICE_KWS_PROVIDER: str = "cpu"
    VOICE_KWS_THRESHOLD: float = 0.35
    VOICE_KWS_SCORE: float = 1.5
    VOICE_KWS_NUM_THREADS: int = 1
    VOICE_KWS_MAX_ACTIVE_PATHS: int = 4
    VOICE_OPENWAKEWORD_MODEL_PATHS: str = ""
    VOICE_OPENWAKEWORD_THRESHOLD: float = 0.6

    # 系统配置
    LOG_LEVEL: str = "INFO"
    SIMULATION_MODE: bool = False
    ROBOT_DATA_DIR: str = "data"
    ACTIONS_LIBRARY_PATH: str = ""
    TASKS_DIRECTORY: str = ""
    SKILL_LIBRARY_PATH: str = ""
    DATA_COLLECTION_FPS: int = 30
    DATA_COLLECTION_CAMERA_INDEX: int = 0
    DATA_COLLECTION_ARMS: tuple[str, ...] = ("left", "right")
    DATA_COLLECTION_SAVE_PATH: str = "data/demos"
    DATA_COLLECTION_FORMAT_VARIANT: str = "portable_simplified"
    DATA_COLLECTION_MIN_FREE_BYTES: int = 1_073_741_824
    DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR: float = 1.25
    DATA_COLLECTION_STALE_WRITE_SECONDS: float = 3600.0
    DATA_COLLECTION_RANDOM_SEED: int = 42
    DATA_COLLECTION_STOP_TIMEOUT_SECONDS: float = 5.0
    DATA_COLLECTION_MAX_SYNC_SKEW_MS: float = 100.0
    DATA_COLLECTION_CAMERA_EXTRINSICS: tuple[float, ...] = ()
    DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME: str = ""
    DATA_COLLECTION_CALIBRATION_ID: str = ""

    # RealSense 相机配置
    CAMERA_PROVIDER: str = "auto"
    REALSENSE_DEVICE_SN: str = ""
    REALSENSE_DEVICE_NAMES: str = ""
    REALSENSE_COLOR_WIDTH: int = 640
    REALSENSE_COLOR_HEIGHT: int = 480
    REALSENSE_DEPTH_WIDTH: int = 640
    REALSENSE_DEPTH_HEIGHT: int = 480
    REALSENSE_FPS: int = 0
    REALSENSE_JPEG_QUALITY: int = 85
    REALSENSE_ALIGN_DEPTH_TO_COLOR: bool = True
    CAMERA_ENCODE_FPS: int = 5
    WEBCAM_DEVICE_INDEXES: str = "0"
    WEBCAM_DEVICE_NAMES: str = ""
    WEBCAM_WIDTH: int = 640
    WEBCAM_HEIGHT: int = 480
    WEBCAM_FPS: int = 30
    WEBCAM_JPEG_QUALITY: int = 85
    VISION_CAMERA_HOST: str = "localhost"
    VISION_CAMERA_PORT: int = 12345
    YOLO_MODEL_PATH: str = "models/best.pt"
    SAM_MODEL_PATH: str = "models/sam2.1_l.pt"
    VISION_DEBUG_SAVE_DIR: str = "pictures"
    BALANCE_CAMERA_INDEX: int = 12
    BALANCE_REQUEST_TIMEOUT_SECONDS: float = 30.0
    VVEAI_API_KEY: str = ""
    VVEAI_BASE_URL: str = "https://api.vveai.com/v1"
    VVEAI_MODEL: str = "doubao-seed-1-8-251228"

    # 手眼标定参数
    VISION_ROTATION_MATRIX: list = None
    VISION_TRANSLATION_VECTOR: list = None
    VISION_GRIPPER_OFFSET: list = None

    # 视觉抓取默认参数
    VISION_DEFAULT_CONFIDENCE: float = 0.7
    VISION_DEFAULT_VELOCITY: int = 15
    VISION_DEFAULT_GRIPPER_LENGTH: float = 150.0
    VISION_DEFAULT_WORKFLOW: str = "bottle"
    VISION_CAMERA_NAME: str = ""
    VISION_PREP_OFFSET_X: float = -0.07
    VISION_GRASP_Z: float = -0.24
    VISION_BOTTLE_TARGET_OFFSET_X: float = -0.025
    VISION_BOTTLE_TARGET_OFFSET_Y: float = 0.015
    VISION_GMM_COMPONENTS: int = 1

    # 视觉重定位 / Tag 补偿参数
    VISION_RELOCALIZATION_STATIONS_FILE: str = "data/vision_stations/profiles.json"
    VISION_RELOCALIZATION_LEFT_CAMERA_NAME: str = ""
    VISION_RELOCALIZATION_RIGHT_CAMERA_NAME: str = ""
    VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX: list = None
    VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX: list = None
    VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION: list = None
    VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION: list = None
    VISION_RELOCALIZATION_LEFT_DIST_COEFFS: list = None
    VISION_RELOCALIZATION_RIGHT_DIST_COEFFS: list = None
    VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH: float = 0.158
    VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT: float = 0.158
    VISION_RELOCALIZATION_POSE_ROTATION_TYPE: str = "rpy"
    VISION_RELOCALIZATION_POSE_ANGLE_UNIT: str = "rad"
    VISION_RELOCALIZATION_LEFT_T_E_C: list = None
    VISION_RELOCALIZATION_RIGHT_T_E_C: list = None
    VISION_RELOCALIZATION_MODE: str = "planar"
    VISION_RELOCALIZATION_PLANAR_CONSTRAINT: str = "none"
    VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES: bool = True
    VISION_RELOCALIZATION_DEBUG_DIR: str = "data/vision_stations/debug"

    # 机械臂配置
    ROBOT1_IP: str = "192.168.3.18"
    ROBOT1_PORT: int = 8080
    ROBOT1_INITIAL_POSE: list = None
    ROBOT2_IP: str = "192.168.3.19"
    ROBOT2_PORT: int = 8080
    ROBOT2_INITIAL_POSE: list = None
    ROBOT_PROVIDER: str = "realman"
    ROBOT_MODEL: str = "rm75-dual"
    ROBOT_TOOL_RACK_ARM: str = "right"
    ROBOT_TOOL_RACK_SLOT_1_APPROACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_1_ATTACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_1_DETACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_1_ATTACH_DWELL_SECONDS: float = 0.5
    ROBOT_TOOL_RACK_SLOT_1_DETACH_DWELL_SECONDS: float = 1.0
    ROBOT_TOOL_RACK_SLOT_2_APPROACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_2_ATTACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_2_DETACH_POSE: list = None
    ROBOT_TOOL_RACK_SLOT_2_ATTACH_DWELL_SECONDS: float = 0.5
    ROBOT_TOOL_RACK_SLOT_2_DETACH_DWELL_SECONDS: float = 0.5
    MOVE_CONTROLLER_HOST: str = "192.168.1.216"
    MOVE_CONTROLLER_PORT: int = 12345
    MOVE_CONTROLLER_CLIENT_BIND_PORT: int = None
    MOVE_VELOCITY: int = 10
    MOVE_RADIUS: int = 0
    MOVE_CONNECT: int = 0
    MOVE_BLOCK: int = 1
    EXECUTION_ACTION_TIMEOUT_SECONDS: float = 600.0
    EXECUTION_ARM_MOVE_MAX_ATTEMPTS: int = 3
    EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS: float = 0.5
    EXECUTION_BODY_POLL_INTERVAL_SECONDS: float = 0.1
    EXECUTION_GRIPPER_MAX_ATTEMPTS: int = 3
    EXECUTION_GRIPPER_RETRY_DELAY_SECONDS: float = 0.5
    EXECUTION_TRAJECTORY_POLL_INTERVAL_SECONDS: float = 0.5
    SAFETY_STOP_WAIT_TIMEOUT_SECONDS: float = 2.0
    MAX_ATTEMPTS: int = 5
    GRIPPER_PICK_SPEED: int = 200
    GRIPPER_PICK_FORCE: int = 1000
    GRIPPER_PICK_TIMEOUT: int = 3
    GRIPPER_RELEASE_SPEED: int = 100
    GRIPPER_RELEASE_TIMEOUT: int = 3

    # 串口设备配置
    BODY_SERIAL_PORT: str = "/dev/ttyUSB1"
    BODY_BAUDRATE: int = 115200
    BODY_SLAVE_ID: int = 1
    BODY_TIMEOUT: int = 1
    BODY_DI_PAN: bool = False
    KUAIHUANSHOU_SERIAL_PORT: str = "/dev/ttyUSB2"
    KUAIHUANSHOU_BAUDRATE: int = 115200
    KUAIHUANSHOU_TIMEOUT: int = 3
    ADP_SERIAL_PORT: str = "/dev/ttyUSB2"
    ADP_BAUDRATE: int = 115200
    ADP_TIMEOUT: int = 5
    ADP_MAX_RETRIES: int = 3
    RELAY_SERIAL_PORT: str = "/dev/ttyUSB0"
    RELAY_BAUDRATE: int = 38400
    RELAY_TIMEOUT: int = 1

    # 表情屏 T5L DGUSII 配置（可选，按需初始化串口）
    EXPRESSION_DISPLAY_ENABLED: bool = False
    EXPRESSION_DISPLAY_PROVIDER: str = "t5l_dgusii"
    EXPRESSION_DISPLAY_CONFIG: str = ""
    EXPRESSION_DISPLAY_SERIAL_PORT: str = "COM4"
    EXPRESSION_DISPLAY_BAUDRATE: int = 115200
    EXPRESSION_DISPLAY_TIMEOUT: float = 0.5
    EXPRESSION_DISPLAY_WRITE_TIMEOUT: float = 1.0
    EXPRESSION_DISPLAY_VP_ADDR: str = "0x5602"
    EXPRESSION_DISPLAY_SP_ADDR: str = "0x8000"
    EXPRESSION_DISPLAY_START_VALUE: str = "0x0000"
    EXPRESSION_DISPLAY_STOP_VALUE: str = "0x0001"
    EXPRESSION_DISPLAY_HIDE_VALUE: str = "0x0002"
    EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH: str = "stop"
    EXPRESSION_DISPLAY_SWITCH_DELAY: float = 0.1
    EXPRESSION_DISPLAY_UPDATE_ICON_RANGE: bool = True
    EXPRESSION_DISPLAY_EXPRESSIONS: str = "happy:24:0:63,sad:27:0:63,angry:30:0:63,speechless:33:0:63,default_1:36:0:63,default_2:39:0:63"
    EXPRESSION_DISPLAY_CLEAR_VPS: str = ""
    EXPRESSION_DISPLAY_TEST_INTERVAL: float = 1.5
    EXPRESSION_DISPLAY_TX_DELAY: float = 0.05

    # 加粉装置配置
    TAPPING_SERIAL_PORT: str = "/dev/ttyACM0"
    TAPPING_BAUDRATE: int = 115200
    TAPPING_TIMEOUT: float = 0.5
    TAPPING_GRIPPER_ADDRESS: int = 9
    TAPPING_LIFT_ADDRESS: int = 7
    TAPPING_ROTATION_ADDRESS: int = 6
    TAPPING_LIFT_SAFE_POSITION: int = 0
    TAPPING_LIFT_DISPENSE_POSITION: int = 50000
    TAPPING_ROTATION_HOME_POSITION: int = 0
    POWDER_DISPENSE_LARGE_STEP: int = 20000
    POWDER_DISPENSE_MEDIUM_STEP: int = 8000
    POWDER_DISPENSE_SMALL_STEP: int = 2000
    POWDER_DISPENSE_MICRO_STEP: int = 500

    # PWM 颈部舵机配置
    PWM_NECK_SERIAL_PORT: str = "/dev/neck"
    PWM_NECK_BAUDRATE: int = 9600
    PWM_NECK_H_SERVO_ID: int = 0
    PWM_NECK_H_INITIAL_PWM: int = 1600
    PWM_NECK_H_PWM_MIN: int = 1100
    PWM_NECK_H_PWM_MAX: int = 2100
    PWM_NECK_H_DEFAULT_TIME: int = 1500
    PWM_NECK_V_SERVO_ID: int = 1
    PWM_NECK_V_INITIAL_PWM: int = 1600
    PWM_NECK_V_PWM_MIN: int = 1200
    PWM_NECK_V_PWM_MAX: int = 1700
    PWM_NECK_V_DEFAULT_TIME: int = 2500

    # WebSocket 服务器配置
    WEBSOCKET_ENABLED: bool = True
    WEBSOCKET_HOST: str = "127.0.0.1"
    WEBSOCKET_PORT: int = 8765
    WEBSOCKET_AUTH_TOKEN: str = ""
    WEBSOCKET_CONTROL_LEASE_SECONDS: float = 30.0
    WEBSOCKET_MAX_MESSAGE_SIZE_BYTES: int = 1048576
    WEBSOCKET_MAX_REQUESTS_PER_SECOND: int = 120
    WEBSOCKET_MAX_CONCURRENT_REQUESTS: int = 16
    WEBSOCKET_MAX_QUEUED_MESSAGES: int = 16
    WEBSOCKET_SEND_TIMEOUT_SECONDS: float = 2.0
    WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS: float = 0.5
    WEBSOCKET_ALLOWED_ORIGINS: tuple[str, ...] = ()
    WEBSOCKET_TLS_CERTIFICATE_PATH: str = ""
    WEBSOCKET_TLS_PRIVATE_KEY_PATH: str = ""
    WEBSOCKET_REVERSE_PROXY_MODE: bool = False
    TELEOPERATION_COMMAND_TIMEOUT_SECONDS: float = 1.0
    AUXILIARY_SERVICE_START_TIMEOUT_SECONDS: float = 5.0
    AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS: float = 10.0

    # MiniCPM Realtime / 聊天配置
    MINICPM_GATEWAY_HOST: str = "localhost"
    MINICPM_GATEWAY_PORT: int = 8006
    MINICPM_WS_SCHEME: str = "wss"
    MINICPM_GATEWAY_PATH_PREFIX: str = ""
    MINICPM_REALTIME_PATH: str = "/v1/realtime"
    MINICPM_MODEL: str = "minicpm-o"
    MINICPM_ASK_ENABLED: bool = True
    MINICPM_ASK_API_KEY: str = ""
    MINICPM_ASK_BASE_URL: str = ""
    MINICPM_ASK_MODEL: str = "gpt-4o-mini"

    # 位置配置
    INITIAL_POSE: list = None
    LEFT_INITIAL_POSE: list = None
    RIGHT_INITIAL_POSE: list = None
    PLACE_DROP_HEIGHT: float = 0.06
    PLACE_ABOVE: list = None
    PLACE_POS2: list = None
    PLACE_TRANSFER_POSE: list = None

    @classmethod
    def load(cls, env_path: Optional[str] = None) -> "_EnvironmentConfig":
        """Load a complete snapshot without exposing rejected raw values."""
        try:
            return cls._load_unchecked(env_path)
        except ConfigLoadError:
            raise
        except (TypeError, ValueError) as exc:
            raise ConfigLoadError(
                "配置包含无法解析的值；请对照 config.env.example 检查类型和格式"
            ) from exc

    @classmethod
    def _load_unchecked(
        cls,
        env_path: Optional[str] = None,
    ) -> "_EnvironmentConfig":
        """
        从 .env 文件加载配置

        Args:
            env_path: 可选，.env 文件路径。默认为项目根目录下的 config.env
        """
        if env_path is None:
            # 默认查找项目根目录的 config.env
            _src_dir = Path(__file__).parent.parent.parent
            env_path = _src_dir / "config.env"
        else:
            env_path = Path(env_path)

        # 优先从指定路径加载
        if env_path.exists():
            load_dotenv(env_path, override=False)
        else:
            # 尝试从项目根目录加载
            _src_dir = Path(__file__).parent.parent.parent
            default_env = _src_dir / "config.env"
            if default_env.exists():
                load_dotenv(default_env, override=False)

        instance = super().__new__(cls)
        instance.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        instance.OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
        instance.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
        instance.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
        instance.DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "")
        instance.DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "")
        instance.DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
        instance.DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "")
        instance.DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "")
        instance.LLM_DEFAULT_PROVIDER = os.getenv("LLM_DEFAULT_PROVIDER", "openai")
        instance.LLM_DEFAULT_TEMPERATURE = float(os.getenv("LLM_DEFAULT_TEMPERATURE", "0.3"))
        instance.LLM_DEFAULT_MAX_TOKENS = int(os.getenv("LLM_DEFAULT_MAX_TOKENS", "512"))
        instance.LLM_REQUEST_TIMEOUT_S = float(os.getenv("LLM_REQUEST_TIMEOUT_S", "60"))
        instance.LLM_FALLBACK_PROVIDERS = tuple(
            dict.fromkeys(
                provider.strip().lower()
                for provider in os.getenv("LLM_FALLBACK_PROVIDERS", "").split(",")
                if provider.strip()
            )
        )
        instance.LLM_CIRCUIT_FAILURE_THRESHOLD = int(
            os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "3")
        )
        instance.LLM_CIRCUIT_RECOVERY_SECONDS = float(
            os.getenv("LLM_CIRCUIT_RECOVERY_SECONDS", "30")
        )
        instance.INTERACTION_TURN_TIMEOUT_S = float(os.getenv("INTERACTION_TURN_TIMEOUT_S", "90"))
        instance.COMMAND_PREVIEW_TTL_SECONDS = float(
            os.getenv("COMMAND_PREVIEW_TTL_SECONDS", "120")
        )
        instance.VOICE_SESSION_TIMEOUT_S = float(os.getenv("VOICE_SESSION_TIMEOUT_S", "30"))
        instance.VOICE_SESSION_HISTORY_TURNS = int(os.getenv("VOICE_SESSION_HISTORY_TURNS", "6"))
        instance.VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S = float(
            os.getenv("VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S", "30")
        )
        instance.VOICE_TTS_ENABLED = os.getenv("VOICE_TTS_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        instance.VOICE_INPUT_ENABLED = os.getenv("VOICE_INPUT_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        instance.VOICE_AUDIO_SAMPLE_RATE = int(os.getenv("VOICE_AUDIO_SAMPLE_RATE", "16000"))
        instance.VOICE_AUDIO_CHANNELS = int(os.getenv("VOICE_AUDIO_CHANNELS", "1"))
        instance.VOICE_AUDIO_BLOCK_MS = int(os.getenv("VOICE_AUDIO_BLOCK_MS", "100"))
        instance.VOICE_AUDIO_QUEUE_SIZE = int(os.getenv("VOICE_AUDIO_QUEUE_SIZE", "300"))
        instance.VOICE_AUDIO_LATENCY = os.getenv("VOICE_AUDIO_LATENCY", "high")
        instance.VOICE_AUDIO_DEVICE = os.getenv("VOICE_AUDIO_DEVICE", "")
        instance.VOICE_AUDIO_SHOW_STATUS = os.getenv(
            "VOICE_AUDIO_SHOW_STATUS", "false"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_VAD_MODEL = os.getenv("VOICE_VAD_MODEL", "fsmn-vad")
        instance.VOICE_VAD_CHUNK_MS = int(os.getenv("VOICE_VAD_CHUNK_MS", "200"))
        instance.VOICE_MIN_UTTERANCE_MS = int(os.getenv("VOICE_MIN_UTTERANCE_MS", "500"))
        instance.VOICE_MAX_UTTERANCE_MS = int(os.getenv("VOICE_MAX_UTTERANCE_MS", "30000"))
        instance.VOICE_END_SILENCE_MS = int(os.getenv("VOICE_END_SILENCE_MS", "800"))
        instance.VOICE_SPEECH_START_RMS_THRESHOLD = float(
            os.getenv("VOICE_SPEECH_START_RMS_THRESHOLD", "0.025")
        )
        instance.VOICE_SPEECH_START_CONFIRM_CHUNKS = int(
            os.getenv("VOICE_SPEECH_START_CONFIRM_CHUNKS", "1")
        )
        instance.VOICE_LISTENING_TIMEOUT_S = float(os.getenv("VOICE_LISTENING_TIMEOUT_S", "8.0"))
        instance.VOICE_FOLLOW_UP_LISTENING_TIMEOUT_S = float(
            os.getenv("VOICE_FOLLOW_UP_LISTENING_TIMEOUT_S", "25.0")
        )
        instance.VOICE_WAKE_COOLDOWN_S = float(os.getenv("VOICE_WAKE_COOLDOWN_S", "1.5"))
        instance.VOICE_WAKE_FEEDBACK_ENABLED = os.getenv(
            "VOICE_WAKE_FEEDBACK_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_WAKE_FEEDBACK_TEXT = os.getenv(
            "VOICE_WAKE_FEEDBACK_TEXT", "明德博士在，请说。"
        )
        instance.VOICE_WAKE_WELCOME_ENABLED = os.getenv(
            "VOICE_WAKE_WELCOME_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_WAKE_WELCOME_TASK = os.getenv("VOICE_WAKE_WELCOME_TASK", "")
        instance.VOICE_SILENCE_RMS_THRESHOLD = float(
            os.getenv("VOICE_SILENCE_RMS_THRESHOLD", "0.01")
        )
        instance.VOICE_SUPPRESS_MODEL_OUTPUT = os.getenv(
            "VOICE_SUPPRESS_MODEL_OUTPUT", "true"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_SHOW_ASR_TIMING = os.getenv("VOICE_SHOW_ASR_TIMING", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        instance.VOICE_ASR_MODEL = os.getenv("VOICE_ASR_MODEL", "iic/SenseVoiceSmall")
        instance.VOICE_ASR_PUNC_MODEL = os.getenv("VOICE_ASR_PUNC_MODEL", "ct-punc")
        instance.VOICE_ASR_DEVICE = os.getenv("VOICE_ASR_DEVICE", "")
        instance.VOICE_ASR_BATCH_SIZE_S = int(os.getenv("VOICE_ASR_BATCH_SIZE_S", "60"))
        instance.VOICE_WAKE_ENGINE = os.getenv("VOICE_WAKE_ENGINE", "sherpa")
        instance.VOICE_WAKE_AUTO_TRIGGER = os.getenv(
            "VOICE_WAKE_AUTO_TRIGGER", "false"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_KWS_ENCODER = os.getenv(
            "VOICE_KWS_ENCODER",
            "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        )
        instance.VOICE_KWS_DECODER = os.getenv(
            "VOICE_KWS_DECODER",
            "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        )
        instance.VOICE_KWS_JOINER = os.getenv(
            "VOICE_KWS_JOINER",
            "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
        )
        instance.VOICE_KWS_TOKENS = os.getenv(
            "VOICE_KWS_TOKENS",
            "models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/tokens.txt",
        )
        instance.VOICE_KWS_KEYWORDS_FILE = os.getenv(
            "VOICE_KWS_KEYWORDS_FILE", "models/kws/keywords.txt"
        )
        instance.VOICE_KWS_PROVIDER = os.getenv("VOICE_KWS_PROVIDER", "cpu")
        instance.VOICE_KWS_THRESHOLD = float(os.getenv("VOICE_KWS_THRESHOLD", "0.35"))
        instance.VOICE_KWS_SCORE = float(os.getenv("VOICE_KWS_SCORE", "1.5"))
        instance.VOICE_KWS_NUM_THREADS = int(os.getenv("VOICE_KWS_NUM_THREADS", "1"))
        instance.VOICE_KWS_MAX_ACTIVE_PATHS = int(os.getenv("VOICE_KWS_MAX_ACTIVE_PATHS", "4"))
        instance.VOICE_OPENWAKEWORD_MODEL_PATHS = os.getenv("VOICE_OPENWAKEWORD_MODEL_PATHS", "")
        instance.VOICE_OPENWAKEWORD_THRESHOLD = float(
            os.getenv("VOICE_OPENWAKEWORD_THRESHOLD", "0.6")
        )
        instance.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        instance.SIMULATION_MODE = os.getenv("SIMULATION_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        instance.ROBOT_DATA_DIR = os.getenv("ROBOT_DATA_DIR", "data")
        instance.ACTIONS_LIBRARY_PATH = os.getenv("ACTIONS_LIBRARY_PATH", "")
        instance.TASKS_DIRECTORY = os.getenv("TASKS_DIRECTORY", "")
        instance.SKILL_LIBRARY_PATH = os.getenv("SKILL_LIBRARY_PATH", "")
        instance.DATA_COLLECTION_FPS = int(os.getenv("DATA_COLLECTION_FPS", "30"))
        instance.DATA_COLLECTION_CAMERA_INDEX = int(os.getenv("DATA_COLLECTION_CAMERA_INDEX", "0"))
        instance.DATA_COLLECTION_ARMS = tuple(
            item.strip()
            for item in os.getenv(
                "DATA_COLLECTION_ARMS",
                "left,right",
            ).split(",")
            if item.strip()
        )
        instance.DATA_COLLECTION_SAVE_PATH = os.getenv(
            "DATA_COLLECTION_SAVE_PATH",
            "data/demos",
        )
        instance.DATA_COLLECTION_FORMAT_VARIANT = os.getenv(
            "DATA_COLLECTION_FORMAT_VARIANT",
            "portable_simplified",
        )
        instance.DATA_COLLECTION_MIN_FREE_BYTES = int(
            os.getenv("DATA_COLLECTION_MIN_FREE_BYTES", "1073741824")
        )
        instance.DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR = float(
            os.getenv(
                "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR",
                "1.25",
            )
        )
        instance.DATA_COLLECTION_STALE_WRITE_SECONDS = float(
            os.getenv("DATA_COLLECTION_STALE_WRITE_SECONDS", "3600")
        )
        instance.DATA_COLLECTION_RANDOM_SEED = int(os.getenv("DATA_COLLECTION_RANDOM_SEED", "42"))
        instance.DATA_COLLECTION_STOP_TIMEOUT_SECONDS = float(
            os.getenv("DATA_COLLECTION_STOP_TIMEOUT_SECONDS", "5")
        )
        instance.DATA_COLLECTION_MAX_SYNC_SKEW_MS = float(
            os.getenv("DATA_COLLECTION_MAX_SYNC_SKEW_MS", "100")
        )
        instance.DATA_COLLECTION_CAMERA_EXTRINSICS = cls._parse_optional_float_tuple(
            os.getenv("DATA_COLLECTION_CAMERA_EXTRINSICS", ""),
            name="DATA_COLLECTION_CAMERA_EXTRINSICS",
            expected_length=16,
        )
        instance.DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME = os.getenv(
            "DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME",
            "",
        )
        instance.DATA_COLLECTION_CALIBRATION_ID = os.getenv(
            "DATA_COLLECTION_CALIBRATION_ID",
            "",
        )
        instance.CAMERA_PROVIDER = os.getenv("CAMERA_PROVIDER", "auto")
        instance.REALSENSE_DEVICE_SN = os.getenv("REALSENSE_DEVICE_SN", "")
        instance.REALSENSE_DEVICE_NAMES = os.getenv("REALSENSE_DEVICE_NAMES", "")
        instance.REALSENSE_COLOR_WIDTH = int(os.getenv("REALSENSE_COLOR_WIDTH", "640"))
        instance.REALSENSE_COLOR_HEIGHT = int(os.getenv("REALSENSE_COLOR_HEIGHT", "480"))
        instance.REALSENSE_DEPTH_WIDTH = int(os.getenv("REALSENSE_DEPTH_WIDTH", "640"))
        instance.REALSENSE_DEPTH_HEIGHT = int(os.getenv("REALSENSE_DEPTH_HEIGHT", "480"))
        instance.REALSENSE_FPS = int(os.getenv("REALSENSE_FPS", "0"))
        instance.REALSENSE_JPEG_QUALITY = int(os.getenv("REALSENSE_JPEG_QUALITY", "85"))
        instance.REALSENSE_ALIGN_DEPTH_TO_COLOR = os.getenv(
            "REALSENSE_ALIGN_DEPTH_TO_COLOR",
            "true",
        ).lower() in ("true", "1", "yes")
        instance.CAMERA_ENCODE_FPS = int(os.getenv("CAMERA_ENCODE_FPS", "5"))
        instance.WEBCAM_DEVICE_INDEXES = os.getenv("WEBCAM_DEVICE_INDEXES", "0")
        instance.WEBCAM_DEVICE_NAMES = os.getenv("WEBCAM_DEVICE_NAMES", "")
        instance.WEBCAM_WIDTH = int(os.getenv("WEBCAM_WIDTH", "640"))
        instance.WEBCAM_HEIGHT = int(os.getenv("WEBCAM_HEIGHT", "480"))
        instance.WEBCAM_FPS = int(os.getenv("WEBCAM_FPS", "30"))
        instance.WEBCAM_JPEG_QUALITY = int(os.getenv("WEBCAM_JPEG_QUALITY", "85"))

        # RealSense 相机配置
        instance.VISION_CAMERA_HOST = os.getenv("VISION_CAMERA_HOST", "localhost")
        instance.VISION_CAMERA_PORT = int(os.getenv("VISION_CAMERA_PORT", "12345"))
        instance.YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "models/best.pt")
        instance.SAM_MODEL_PATH = os.getenv("SAM_MODEL_PATH", "models/sam2.1_l.pt")
        instance.VISION_DEBUG_SAVE_DIR = os.getenv("VISION_DEBUG_SAVE_DIR", "pictures")
        instance.BALANCE_CAMERA_INDEX = int(os.getenv("BALANCE_CAMERA_INDEX", "12"))
        instance.BALANCE_REQUEST_TIMEOUT_SECONDS = float(
            os.getenv("BALANCE_REQUEST_TIMEOUT_SECONDS", "30")
        )
        instance.VVEAI_API_KEY = os.getenv("VVEAI_API_KEY", "")
        instance.VVEAI_BASE_URL = os.getenv(
            "VVEAI_BASE_URL",
            "https://api.vveai.com/v1",
        )
        instance.VVEAI_MODEL = os.getenv(
            "VVEAI_MODEL",
            "doubao-seed-1-8-251228",
        )

        # 手眼标定参数
        instance.VISION_ROTATION_MATRIX = cls._parse_float_list(
            os.getenv(
                "VISION_ROTATION_MATRIX",
                "0.00215684,0.97503835,0.22202606,-0.99995231,-0.0000119,0.00976617,0.00952503,-0.22203654,0.97499182",
            )
        )
        instance.VISION_TRANSLATION_VECTOR = cls._parse_float_list(
            os.getenv("VISION_TRANSLATION_VECTOR", "-0.10273135,0.03312807,-0.07214614")
        )
        instance.VISION_GRIPPER_OFFSET = cls._parse_float_list(
            os.getenv("VISION_GRIPPER_OFFSET", "3.146,0.0,3.128")
        )
        instance.VISION_DEFAULT_CONFIDENCE = float(os.getenv("VISION_DEFAULT_CONFIDENCE", "0.7"))
        instance.VISION_DEFAULT_VELOCITY = int(os.getenv("VISION_DEFAULT_VELOCITY", "15"))
        instance.VISION_DEFAULT_GRIPPER_LENGTH = float(
            os.getenv("VISION_DEFAULT_GRIPPER_LENGTH", "150.0")
        )
        instance.VISION_DEFAULT_WORKFLOW = os.getenv("VISION_DEFAULT_WORKFLOW", "bottle")
        instance.VISION_CAMERA_NAME = os.getenv("VISION_CAMERA_NAME", "")
        instance.VISION_PREP_OFFSET_X = float(os.getenv("VISION_PREP_OFFSET_X", "-0.07"))
        instance.VISION_GRASP_Z = float(os.getenv("VISION_GRASP_Z", "-0.24"))
        instance.VISION_BOTTLE_TARGET_OFFSET_X = float(
            os.getenv("VISION_BOTTLE_TARGET_OFFSET_X", "-0.025")
        )
        instance.VISION_BOTTLE_TARGET_OFFSET_Y = float(
            os.getenv("VISION_BOTTLE_TARGET_OFFSET_Y", "0.015")
        )
        instance.VISION_GMM_COMPONENTS = int(os.getenv("VISION_GMM_COMPONENTS", "1"))

        # 视觉重定位 / Tag 补偿参数
        instance.VISION_RELOCALIZATION_STATIONS_FILE = os.getenv(
            "VISION_RELOCALIZATION_STATIONS_FILE",
            "data/vision_stations/profiles.json",
        )
        instance.VISION_RELOCALIZATION_LEFT_CAMERA_NAME = os.getenv(
            "VISION_RELOCALIZATION_LEFT_CAMERA_NAME",
            instance.VISION_CAMERA_NAME,
        )
        instance.VISION_RELOCALIZATION_RIGHT_CAMERA_NAME = os.getenv(
            "VISION_RELOCALIZATION_RIGHT_CAMERA_NAME",
            instance.VISION_CAMERA_NAME,
        )
        default_camera_matrix = "1361.8900146484375,0.0,930.7236938476562,0.0,1361.31640625,547.1578979492188,0.0,0.0,1.0"
        legacy_camera_matrix = cls._parse_float_list(
            os.getenv(
                "VISION_RELOCALIZATION_CAMERA_MATRIX",
                default_camera_matrix,
            )
        )
        legacy_camera_resolution = cls._parse_float_list(
            os.getenv(
                "VISION_RELOCALIZATION_CAMERA_MATRIX_RESOLUTION",
                "1920,1080",
            )
        )
        legacy_dist_coeffs = cls._parse_float_list(
            os.getenv(
                "VISION_RELOCALIZATION_DIST_COEFFS",
                "0,0,0,0,0",
            )
        )
        instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX",
                    "",
                )
            )
            or legacy_camera_matrix
        )
        instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX",
                    "",
                )
            )
            or legacy_camera_matrix
        )
        instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION",
                    "",
                )
            )
            or legacy_camera_resolution
        )
        instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION",
                    "",
                )
            )
            or legacy_camera_resolution
        )
        instance.VISION_RELOCALIZATION_LEFT_DIST_COEFFS = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_LEFT_DIST_COEFFS",
                    "",
                )
            )
            or legacy_dist_coeffs
        )
        instance.VISION_RELOCALIZATION_RIGHT_DIST_COEFFS = (
            cls._parse_float_list(
                os.getenv(
                    "VISION_RELOCALIZATION_RIGHT_DIST_COEFFS",
                    "",
                )
            )
            or legacy_dist_coeffs
        )
        instance.VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH = float(
            os.getenv(
                "VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH",
                os.getenv("VISION_RELOCALIZATION_MARKER_WIDTH", "0.158"),
            )
        )
        instance.VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT = float(
            os.getenv(
                "VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT",
                os.getenv("VISION_RELOCALIZATION_MARKER_HEIGHT", "0.158"),
            )
        )
        instance.VISION_RELOCALIZATION_POSE_ROTATION_TYPE = os.getenv(
            "VISION_RELOCALIZATION_POSE_ROTATION_TYPE", "rpy"
        )
        instance.VISION_RELOCALIZATION_POSE_ANGLE_UNIT = os.getenv(
            "VISION_RELOCALIZATION_POSE_ANGLE_UNIT", "rad"
        )
        default_t_e_c = cls._matrix4_from_rt(
            instance.VISION_ROTATION_MATRIX, instance.VISION_TRANSLATION_VECTOR
        )
        instance.VISION_RELOCALIZATION_LEFT_T_E_C = cls._parse_matrix4(
            os.getenv("VISION_RELOCALIZATION_LEFT_T_E_C", ""),
            default_t_e_c,
        )
        instance.VISION_RELOCALIZATION_RIGHT_T_E_C = cls._parse_matrix4(
            os.getenv("VISION_RELOCALIZATION_RIGHT_T_E_C", ""),
            default_t_e_c,
        )
        instance.VISION_RELOCALIZATION_MODE = os.getenv("VISION_RELOCALIZATION_MODE", "planar")
        instance.VISION_RELOCALIZATION_PLANAR_CONSTRAINT = os.getenv(
            "VISION_RELOCALIZATION_PLANAR_CONSTRAINT", "none"
        )
        instance.VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES = os.getenv(
            "VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES",
            "true",
        ).lower() in ("true", "1", "yes")
        instance.VISION_RELOCALIZATION_DEBUG_DIR = os.getenv(
            "VISION_RELOCALIZATION_DEBUG_DIR",
            "data/vision_stations/debug",
        )

        # 机械臂配置
        instance.ROBOT_PROVIDER = (
            os.getenv(
                "ROBOT_PROVIDER",
                "realman",
            )
            .strip()
            .lower()
        )
        instance.ROBOT_MODEL = os.getenv(
            "ROBOT_MODEL",
            "rm75-dual",
        ).strip()
        instance.ROBOT1_IP = os.getenv("ROBOT1_IP", "192.168.3.18")
        instance.ROBOT1_PORT = int(os.getenv("ROBOT1_PORT", "8080"))
        instance.ROBOT1_INITIAL_POSE = cls._parse_float_list(
            os.getenv("ROBOT1_INITIAL_POSE", "-0.04844,-0.269769,-0.101888,3.109,-0.094,-1.592")
        )
        instance.ROBOT2_IP = os.getenv("ROBOT2_IP", "192.168.3.19")
        instance.ROBOT2_PORT = int(os.getenv("ROBOT2_PORT", "8080"))
        instance.ROBOT2_INITIAL_POSE = cls._parse_float_list(
            os.getenv("ROBOT2_INITIAL_POSE", "-0.053437,0.24741,-0.120801,3.114,-0.032,-2.935")
        )
        instance.ROBOT_TOOL_RACK_ARM = os.getenv(
            "ROBOT_TOOL_RACK_ARM",
            "right",
        ).strip()
        instance.ROBOT_TOOL_RACK_SLOT_1_APPROACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_1_APPROACH_POSE",
                "-0.119418,-0.088287,-0.380201,3.110000,0.001000,-2.893000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_1_ATTACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_1_ATTACH_POSE",
                "-0.119418,-0.088287,-0.480201,3.110000,0.001000,-2.893000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_1_DETACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_1_DETACH_POSE",
                "-0.119418,-0.088287,-0.455201,3.110000,0.001000,-2.893000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_1_ATTACH_DWELL_SECONDS = float(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_1_ATTACH_DWELL_SECONDS",
                "0.5",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_1_DETACH_DWELL_SECONDS = float(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_1_DETACH_DWELL_SECONDS",
                "1.0",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_2_APPROACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_2_APPROACH_POSE",
                "-0.117419,-0.066539,-0.380577,3.135000,-0.009000,-2.869000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_2_ATTACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_2_ATTACH_POSE",
                "-0.117419,-0.066539,-0.480577,3.135000,-0.009000,-2.869000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_2_DETACH_POSE = cls._parse_float_list(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_2_DETACH_POSE",
                "-0.117419,-0.066539,-0.465577,3.135000,-0.009000,-2.869000",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_2_ATTACH_DWELL_SECONDS = float(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_2_ATTACH_DWELL_SECONDS",
                "0.5",
            )
        )
        instance.ROBOT_TOOL_RACK_SLOT_2_DETACH_DWELL_SECONDS = float(
            os.getenv(
                "ROBOT_TOOL_RACK_SLOT_2_DETACH_DWELL_SECONDS",
                "0.5",
            )
        )
        instance.MOVE_CONTROLLER_HOST = os.getenv("MOVE_CONTROLLER_HOST", "192.168.1.216")
        instance.MOVE_CONTROLLER_PORT = int(os.getenv("MOVE_CONTROLLER_PORT", "12345"))
        move_client_bind_port = os.getenv("MOVE_CONTROLLER_CLIENT_BIND_PORT")
        instance.MOVE_CONTROLLER_CLIENT_BIND_PORT = (
            int(move_client_bind_port) if move_client_bind_port else None
        )
        instance.MOVE_VELOCITY = int(os.getenv("MOVE_VELOCITY", "10"))
        instance.MOVE_RADIUS = int(os.getenv("MOVE_RADIUS", "0"))
        instance.MOVE_CONNECT = int(os.getenv("MOVE_CONNECT", "0"))
        instance.MOVE_BLOCK = int(os.getenv("MOVE_BLOCK", "1"))
        instance.EXECUTION_ACTION_TIMEOUT_SECONDS = float(
            os.getenv("EXECUTION_ACTION_TIMEOUT_SECONDS", "600.0")
        )
        instance.EXECUTION_ARM_MOVE_MAX_ATTEMPTS = int(
            os.getenv("EXECUTION_ARM_MOVE_MAX_ATTEMPTS", "3")
        )
        instance.EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS = float(
            os.getenv(
                "EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS",
                "0.5",
            )
        )
        instance.EXECUTION_BODY_POLL_INTERVAL_SECONDS = float(
            os.getenv("EXECUTION_BODY_POLL_INTERVAL_SECONDS", "0.1")
        )
        instance.EXECUTION_GRIPPER_MAX_ATTEMPTS = int(
            os.getenv("EXECUTION_GRIPPER_MAX_ATTEMPTS", "3")
        )
        instance.EXECUTION_GRIPPER_RETRY_DELAY_SECONDS = float(
            os.getenv(
                "EXECUTION_GRIPPER_RETRY_DELAY_SECONDS",
                "0.5",
            )
        )
        instance.EXECUTION_TRAJECTORY_POLL_INTERVAL_SECONDS = float(
            os.getenv(
                "EXECUTION_TRAJECTORY_POLL_INTERVAL_SECONDS",
                "0.5",
            )
        )
        instance.SAFETY_STOP_WAIT_TIMEOUT_SECONDS = float(
            os.getenv("SAFETY_STOP_WAIT_TIMEOUT_SECONDS", "2.0")
        )
        instance.MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
        instance.GRIPPER_PICK_SPEED = int(os.getenv("GRIPPER_PICK_SPEED", "200"))
        instance.GRIPPER_PICK_FORCE = int(os.getenv("GRIPPER_PICK_FORCE", "1000"))
        instance.GRIPPER_PICK_TIMEOUT = int(os.getenv("GRIPPER_PICK_TIMEOUT", "3"))
        instance.GRIPPER_RELEASE_SPEED = int(os.getenv("GRIPPER_RELEASE_SPEED", "100"))
        instance.GRIPPER_RELEASE_TIMEOUT = int(os.getenv("GRIPPER_RELEASE_TIMEOUT", "3"))

        # PWM 颈部舵机
        instance.PWM_NECK_SERIAL_PORT = os.getenv("PWM_NECK_SERIAL_PORT", "/dev/neck")
        instance.PWM_NECK_BAUDRATE = int(os.getenv("PWM_NECK_BAUDRATE", "9600"))
        instance.PWM_NECK_H_SERVO_ID = int(os.getenv("PWM_NECK_H_SERVO_ID", "0"))
        instance.PWM_NECK_H_INITIAL_PWM = int(os.getenv("PWM_NECK_H_INITIAL_PWM", "1600"))
        instance.PWM_NECK_H_PWM_MIN = int(os.getenv("PWM_NECK_H_PWM_MIN", "1100"))
        instance.PWM_NECK_H_PWM_MAX = int(os.getenv("PWM_NECK_H_PWM_MAX", "2100"))
        instance.PWM_NECK_H_DEFAULT_TIME = int(os.getenv("PWM_NECK_H_DEFAULT_TIME", "1500"))
        instance.PWM_NECK_V_SERVO_ID = int(os.getenv("PWM_NECK_V_SERVO_ID", "1"))
        instance.PWM_NECK_V_INITIAL_PWM = int(os.getenv("PWM_NECK_V_INITIAL_PWM", "1600"))
        instance.PWM_NECK_V_PWM_MIN = int(os.getenv("PWM_NECK_V_PWM_MIN", "1200"))
        instance.PWM_NECK_V_PWM_MAX = int(os.getenv("PWM_NECK_V_PWM_MAX", "1700"))
        instance.PWM_NECK_V_DEFAULT_TIME = int(os.getenv("PWM_NECK_V_DEFAULT_TIME", "2500"))

        # 串口设备配置
        instance.BODY_SERIAL_PORT = os.getenv("BODY_SERIAL_PORT", "/dev/ttyUSB1")
        instance.BODY_BAUDRATE = int(os.getenv("BODY_BAUDRATE", "115200"))
        instance.BODY_SLAVE_ID = int(os.getenv("BODY_SLAVE_ID", "1"))
        instance.BODY_TIMEOUT = int(os.getenv("BODY_TIMEOUT", "1"))
        instance.BODY_DI_PAN = os.getenv("BODY_DI_PAN", "false").lower() in ("true", "1", "yes")
        instance.KUAIHUANSHOU_SERIAL_PORT = os.getenv("KUAIHUANSHOU_SERIAL_PORT", "/dev/ttyUSB2")
        instance.KUAIHUANSHOU_BAUDRATE = int(os.getenv("KUAIHUANSHOU_BAUDRATE", "115200"))
        instance.KUAIHUANSHOU_TIMEOUT = int(os.getenv("KUAIHUANSHOU_TIMEOUT", "3"))
        instance.ADP_SERIAL_PORT = os.getenv("ADP_SERIAL_PORT", "/dev/ttyUSB2")
        instance.ADP_BAUDRATE = int(os.getenv("ADP_BAUDRATE", "115200"))
        instance.ADP_TIMEOUT = int(os.getenv("ADP_TIMEOUT", "5"))
        instance.ADP_MAX_RETRIES = int(os.getenv("ADP_MAX_RETRIES", "3"))
        instance.RELAY_SERIAL_PORT = os.getenv("RELAY_SERIAL_PORT", "/dev/ttyUSB0")
        instance.RELAY_BAUDRATE = int(os.getenv("RELAY_BAUDRATE", "38400"))
        instance.RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", "1"))

        # 表情屏 T5L DGUSII（可选，实际调用时才打开串口）
        instance.EXPRESSION_DISPLAY_ENABLED = os.getenv(
            "EXPRESSION_DISPLAY_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
        instance.EXPRESSION_DISPLAY_PROVIDER = os.getenv(
            "EXPRESSION_DISPLAY_PROVIDER", "t5l_dgusii"
        )
        instance.EXPRESSION_DISPLAY_CONFIG = os.getenv("EXPRESSION_DISPLAY_CONFIG", "")
        instance.EXPRESSION_DISPLAY_SERIAL_PORT = os.getenv(
            "EXPRESSION_DISPLAY_SERIAL_PORT", "COM4"
        )
        instance.EXPRESSION_DISPLAY_BAUDRATE = int(
            os.getenv("EXPRESSION_DISPLAY_BAUDRATE", "115200")
        )
        instance.EXPRESSION_DISPLAY_TIMEOUT = float(os.getenv("EXPRESSION_DISPLAY_TIMEOUT", "0.5"))
        instance.EXPRESSION_DISPLAY_WRITE_TIMEOUT = float(
            os.getenv("EXPRESSION_DISPLAY_WRITE_TIMEOUT", "1.0")
        )
        instance.EXPRESSION_DISPLAY_VP_ADDR = os.getenv("EXPRESSION_DISPLAY_VP_ADDR", "0x5602")
        instance.EXPRESSION_DISPLAY_SP_ADDR = os.getenv("EXPRESSION_DISPLAY_SP_ADDR", "0x8000")
        instance.EXPRESSION_DISPLAY_START_VALUE = os.getenv(
            "EXPRESSION_DISPLAY_START_VALUE", "0x0000"
        )
        instance.EXPRESSION_DISPLAY_STOP_VALUE = os.getenv(
            "EXPRESSION_DISPLAY_STOP_VALUE", "0x0001"
        )
        instance.EXPRESSION_DISPLAY_HIDE_VALUE = os.getenv(
            "EXPRESSION_DISPLAY_HIDE_VALUE", "0x0002"
        )
        instance.EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH = os.getenv(
            "EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH", "stop"
        )
        instance.EXPRESSION_DISPLAY_SWITCH_DELAY = float(
            os.getenv("EXPRESSION_DISPLAY_SWITCH_DELAY", "0.1")
        )
        instance.EXPRESSION_DISPLAY_UPDATE_ICON_RANGE = os.getenv(
            "EXPRESSION_DISPLAY_UPDATE_ICON_RANGE", "true"
        ).lower() in ("true", "1", "yes")
        instance.EXPRESSION_DISPLAY_EXPRESSIONS = os.getenv(
            "EXPRESSION_DISPLAY_EXPRESSIONS",
            "happy:24:0:63,sad:27:0:63,angry:30:0:63,speechless:33:0:63,default_1:36:0:63,default_2:39:0:63",
        )
        instance.EXPRESSION_DISPLAY_CLEAR_VPS = os.getenv("EXPRESSION_DISPLAY_CLEAR_VPS", "")
        instance.EXPRESSION_DISPLAY_TEST_INTERVAL = float(
            os.getenv("EXPRESSION_DISPLAY_TEST_INTERVAL", "1.5")
        )
        instance.EXPRESSION_DISPLAY_TX_DELAY = float(
            os.getenv("EXPRESSION_DISPLAY_TX_DELAY", "0.05")
        )

        # 加粉装置
        instance.TAPPING_SERIAL_PORT = os.getenv("TAPPING_SERIAL_PORT", "/dev/ttyACM0")
        instance.TAPPING_BAUDRATE = int(os.getenv("TAPPING_BAUDRATE", "115200"))
        instance.TAPPING_TIMEOUT = float(os.getenv("TAPPING_TIMEOUT", "0.5"))
        instance.TAPPING_GRIPPER_ADDRESS = int(os.getenv("TAPPING_GRIPPER_ADDRESS", "9"))
        instance.TAPPING_LIFT_ADDRESS = int(os.getenv("TAPPING_LIFT_ADDRESS", "7"))
        instance.TAPPING_ROTATION_ADDRESS = int(os.getenv("TAPPING_ROTATION_ADDRESS", "6"))
        instance.TAPPING_LIFT_SAFE_POSITION = int(os.getenv("TAPPING_LIFT_SAFE_POSITION", "0"))
        instance.TAPPING_LIFT_DISPENSE_POSITION = int(
            os.getenv("TAPPING_LIFT_DISPENSE_POSITION", "50000")
        )
        instance.TAPPING_ROTATION_HOME_POSITION = int(
            os.getenv("TAPPING_ROTATION_HOME_POSITION", "0")
        )
        instance.POWDER_DISPENSE_LARGE_STEP = int(os.getenv("POWDER_DISPENSE_LARGE_STEP", "20000"))
        instance.POWDER_DISPENSE_MEDIUM_STEP = int(os.getenv("POWDER_DISPENSE_MEDIUM_STEP", "8000"))
        instance.POWDER_DISPENSE_SMALL_STEP = int(os.getenv("POWDER_DISPENSE_SMALL_STEP", "2000"))
        instance.POWDER_DISPENSE_MICRO_STEP = int(os.getenv("POWDER_DISPENSE_MICRO_STEP", "500"))

        # WebSocket 服务器配置
        instance.WEBSOCKET_ENABLED = os.getenv(
            "WEBSOCKET_ENABLED",
            "true",
        ).lower() in ("true", "1", "yes")
        instance.WEBSOCKET_HOST = os.getenv(
            "WEBSOCKET_HOST",
            "127.0.0.1",
        )
        instance.WEBSOCKET_PORT = int(os.getenv("WEBSOCKET_PORT", "8765"))
        instance.WEBSOCKET_AUTH_TOKEN = os.getenv(
            "WEBSOCKET_AUTH_TOKEN",
            "",
        )
        instance.WEBSOCKET_CONTROL_LEASE_SECONDS = float(
            os.getenv("WEBSOCKET_CONTROL_LEASE_SECONDS", "30.0")
        )
        instance.WEBSOCKET_MAX_MESSAGE_SIZE_BYTES = int(
            os.getenv("WEBSOCKET_MAX_MESSAGE_SIZE_BYTES", "1048576")
        )
        instance.WEBSOCKET_MAX_REQUESTS_PER_SECOND = int(
            os.getenv("WEBSOCKET_MAX_REQUESTS_PER_SECOND", "120")
        )
        instance.WEBSOCKET_MAX_CONCURRENT_REQUESTS = int(
            os.getenv("WEBSOCKET_MAX_CONCURRENT_REQUESTS", "16")
        )
        instance.WEBSOCKET_MAX_QUEUED_MESSAGES = int(
            os.getenv("WEBSOCKET_MAX_QUEUED_MESSAGES", "16")
        )
        instance.WEBSOCKET_SEND_TIMEOUT_SECONDS = float(
            os.getenv("WEBSOCKET_SEND_TIMEOUT_SECONDS", "2.0")
        )
        instance.WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS = float(
            os.getenv("WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS", "0.5")
        )
        instance.WEBSOCKET_ALLOWED_ORIGINS = tuple(
            dict.fromkeys(
                origin.strip()
                for origin in os.getenv("WEBSOCKET_ALLOWED_ORIGINS", "").split(",")
                if origin.strip()
            )
        )
        instance.WEBSOCKET_TLS_CERTIFICATE_PATH = os.getenv(
            "WEBSOCKET_TLS_CERTIFICATE_PATH",
            "",
        )
        instance.WEBSOCKET_TLS_PRIVATE_KEY_PATH = os.getenv(
            "WEBSOCKET_TLS_PRIVATE_KEY_PATH",
            "",
        )
        instance.WEBSOCKET_REVERSE_PROXY_MODE = os.getenv(
            "WEBSOCKET_REVERSE_PROXY_MODE",
            "false",
        ).lower() in ("true", "1", "yes")
        instance.TELEOPERATION_COMMAND_TIMEOUT_SECONDS = float(
            os.getenv("TELEOPERATION_COMMAND_TIMEOUT_SECONDS", "1.0")
        )
        instance.AUXILIARY_SERVICE_START_TIMEOUT_SECONDS = float(
            os.getenv("AUXILIARY_SERVICE_START_TIMEOUT_SECONDS", "5.0")
        )
        instance.AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS = float(
            os.getenv("AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS", "10.0")
        )

        # MiniCPM Realtime / 聊天配置
        instance.MINICPM_GATEWAY_HOST = os.getenv("MINICPM_GATEWAY_HOST", "localhost")
        instance.MINICPM_GATEWAY_PORT = int(os.getenv("MINICPM_GATEWAY_PORT", "8006"))
        instance.MINICPM_WS_SCHEME = os.getenv("MINICPM_WS_SCHEME", "wss")
        instance.MINICPM_GATEWAY_PATH_PREFIX = os.getenv("MINICPM_GATEWAY_PATH_PREFIX", "")
        instance.MINICPM_REALTIME_PATH = os.getenv("MINICPM_REALTIME_PATH", "/v1/realtime")
        instance.MINICPM_MODEL = os.getenv("MINICPM_MODEL", "minicpm-o")
        instance.MINICPM_ASK_ENABLED = os.getenv("MINICPM_ASK_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        instance.MINICPM_ASK_API_KEY = os.getenv("MINICPM_ASK_API_KEY", "")
        instance.MINICPM_ASK_BASE_URL = os.getenv("MINICPM_ASK_BASE_URL", "")
        instance.MINICPM_ASK_MODEL = os.getenv("MINICPM_ASK_MODEL", "gpt-4o-mini")

        # 位置配置
        instance.INITIAL_POSE = cls._parse_float_list(
            os.getenv("INITIAL_POSE", "-0.303379,0.274441,-0.075986,-3.081,0.137,-1.828")
        )
        instance.LEFT_INITIAL_POSE = cls._parse_float_list(
            os.getenv("LEFT_INITIAL_POSE", "-0.356,0.309,-0.186,-3.141,0,-1.89")
        )
        instance.RIGHT_INITIAL_POSE = cls._parse_float_list(
            os.getenv("RIGHT_INITIAL_POSE", "-0.372,0.221,-0.186,-3.121,0,-1.89")
        )
        instance.PLACE_DROP_HEIGHT = float(os.getenv("PLACE_DROP_HEIGHT", "0.06"))
        instance.PLACE_ABOVE = cls._parse_float_list(
            os.getenv("PLACE_ABOVE", "0.0637,-0.07351,-0.4182,3.15,0,1.617")
        )
        instance.PLACE_POS2 = cls._parse_float_list(
            os.getenv("PLACE_POS2", "0.285488,-0.256408,-0.090654,3.14,0,1.5")
        )
        instance.PLACE_TRANSFER_POSE = cls._parse_float_list(
            os.getenv(
                "PLACE_TRANSFER_POSE",
                "0.424,-0.092,-0.439,3.15,0,1.618",
            )
        )

        return instance

    @classmethod
    def _parse_float_list(cls, value: str) -> list:
        """解析逗号分隔的浮点数列表"""
        if not value:
            return []
        try:
            return [float(x.strip()) for x in value.split(",")]
        except (ValueError, AttributeError):
            return []

    @staticmethod
    def _parse_optional_float_tuple(
        value: str,
        *,
        name: str,
        expected_length: int,
    ) -> tuple[float, ...]:
        normalized = value.strip()
        if not normalized:
            return ()
        try:
            values = tuple(float(item.strip()) for item in normalized.split(","))
        except ValueError as exc:
            raise ConfigLoadError(f"{name} must contain comma-separated numbers") from exc
        if len(values) != expected_length:
            raise ConfigLoadError(f"{name} must contain exactly {expected_length} values")
        return values

    @classmethod
    def _matrix4_from_rt(cls, rotation_flat: list, translation: list) -> list:
        rot = rotation_flat or [1, 0, 0, 0, 1, 0, 0, 0, 1]
        trans = translation or [0, 0, 0]
        if len(rot) != 9:
            rot = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        if len(trans) != 3:
            trans = [0, 0, 0]
        return [
            [rot[0], rot[1], rot[2], trans[0]],
            [rot[3], rot[4], rot[5], trans[1]],
            [rot[6], rot[7], rot[8], trans[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    @classmethod
    def _parse_matrix4(cls, value: str, default: list | None = None) -> list:
        values = cls._parse_float_list(value)
        if len(values) != 16:
            return default or cls._matrix4_from_rt([], [])
        return [values[i : i + 4] for i in range(0, 16, 4)]


def load_application_settings(
    env_path: str | None = None,
) -> "ApplicationSettings":
    """Parse environment values once and return immutable domain settings."""
    raw_config = _EnvironmentConfig.load(env_path)
    return ApplicationSettings.from_config(raw_config)
