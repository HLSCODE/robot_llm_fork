"""
配置加载器
统一管理所有配置项，从 config.env 文件加载
"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


class Config:
    """
    单例配置类
    从 .env 文件加载配置，供全局使用
    """
    _instance: Optional['Config'] = None
    _loaded: bool = False

    # 配置项
    # LLM/AI 配置
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_BASE_URL: str = ""
    LLM_DEFAULT_PROVIDER: str = "openai"
    LLM_DEFAULT_TEMPERATURE: float = 0.3
    LLM_DEFAULT_MAX_TOKENS: int = 512
    LLM_REQUEST_TIMEOUT_S: float = 60.0
    VOICE_SESSION_TIMEOUT_S: float = 30.0
    VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S: float = 30.0
    VOICE_AUTO_EXECUTE_COMMAND: bool = False
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
    VOICE_LISTENING_TIMEOUT_S: float = 8.0
    VOICE_WAKE_COOLDOWN_S: float = 1.5
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
    RUN_MODE: str = "server"  # gui / server
    SIMULATION_MODE: bool = False
    SKILL_LIBRARY_PATH: str = "data/skills/skill_library.json"
    
    # RealSense 相机配置
    CAMERA_PROVIDER: str = "auto"
    REALSENSE_DEVICE_SN: str = ""
    REALSENSE_DEVICE_NAMES: str = ""
    WEBCAM_DEVICE_INDEXES: str = "0"
    WEBCAM_DEVICE_NAMES: str = ""
    VISION_CAMERA_HOST: str = "localhost"
    VISION_CAMERA_PORT: int = 12345
    YOLO_MODEL_PATH: str = "models/best.pt"
    SAM_MODEL_PATH: str = "models/sam2.1_l.pt"
    VISION_DEBUG_SAVE_DIR: str = "pictures"

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
    VISION_GMM_COMPONENTS: int = 1
    
    # 机械臂配置
    ROBOT1_IP: str = "192.168.3.19"
    ROBOT1_PORT: int = 8080
    ROBOT1_INITIAL_POSE: list = None
    ROBOT2_IP: str = "192.168.3.18"
    ROBOT2_PORT: int = 8080
    ROBOT2_INITIAL_POSE: list = None
    MOVE_CONTROLLER_HOST: str = "192.168.1.216"
    MOVE_CONTROLLER_PORT: int = 12345
    MOVE_CONTROLLER_CLIENT_BIND_PORT: int = None
    MOVE_SPEED: int = 10
    MOVE_VELOCITY: int = 10
    MOVE_RADIUS: int = 0
    MOVE_CONNECT: int = 0
    MOVE_BLOCK: int = 1
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
    WEBSOCKET_HOST: str = "0.0.0.0"
    WEBSOCKET_PORT: int = 8765

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
    GUN1_POSITIONS: dict = None
    GUN2_POSITIONS: dict = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, env_path: Optional[str] = None) -> 'Config':
        """
        从 .env 文件加载配置

        Args:
            env_path: 可选，.env 文件路径。默认为项目根目录下的 config.env
        """
        if cls._loaded:
            return cls._instance

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

        # 确保实例已创建
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        
        instance = cls._instance
        instance.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        instance.OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
        instance.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
        instance.LLM_DEFAULT_PROVIDER = os.getenv("LLM_DEFAULT_PROVIDER", "openai")
        instance.LLM_DEFAULT_TEMPERATURE = float(os.getenv("LLM_DEFAULT_TEMPERATURE", "0.3"))
        instance.LLM_DEFAULT_MAX_TOKENS = int(os.getenv("LLM_DEFAULT_MAX_TOKENS", "512"))
        instance.LLM_REQUEST_TIMEOUT_S = float(os.getenv("LLM_REQUEST_TIMEOUT_S", "60"))
        instance.VOICE_SESSION_TIMEOUT_S = float(os.getenv("VOICE_SESSION_TIMEOUT_S", "30"))
        instance.VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S = float(os.getenv(
            "VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S", "30"
        ))
        instance.VOICE_AUTO_EXECUTE_COMMAND = os.getenv(
            "VOICE_AUTO_EXECUTE_COMMAND", "false"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_TTS_ENABLED = os.getenv(
            "VOICE_TTS_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_INPUT_ENABLED = os.getenv(
            "VOICE_INPUT_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
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
        instance.VOICE_LISTENING_TIMEOUT_S = float(os.getenv("VOICE_LISTENING_TIMEOUT_S", "8.0"))
        instance.VOICE_WAKE_COOLDOWN_S = float(os.getenv("VOICE_WAKE_COOLDOWN_S", "1.5"))
        instance.VOICE_SILENCE_RMS_THRESHOLD = float(os.getenv("VOICE_SILENCE_RMS_THRESHOLD", "0.01"))
        instance.VOICE_SUPPRESS_MODEL_OUTPUT = os.getenv(
            "VOICE_SUPPRESS_MODEL_OUTPUT", "true"
        ).lower() in ("true", "1", "yes")
        instance.VOICE_SHOW_ASR_TIMING = os.getenv(
            "VOICE_SHOW_ASR_TIMING", "false"
        ).lower() in ("true", "1", "yes")
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
        instance.VOICE_OPENWAKEWORD_THRESHOLD = float(os.getenv("VOICE_OPENWAKEWORD_THRESHOLD", "0.6"))
        instance.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        instance.RUN_MODE = os.getenv("RUN_MODE", "server")
        instance.SIMULATION_MODE = os.getenv("SIMULATION_MODE", "false").lower() in ("true", "1", "yes")
        instance.SKILL_LIBRARY_PATH = os.getenv("SKILL_LIBRARY_PATH", "data/skills/skill_library.json")
        instance.CAMERA_PROVIDER = os.getenv("CAMERA_PROVIDER", "auto")
        instance.REALSENSE_DEVICE_SN = os.getenv("REALSENSE_DEVICE_SN", "")
        instance.REALSENSE_DEVICE_NAMES = os.getenv("REALSENSE_DEVICE_NAMES", "")
        instance.WEBCAM_DEVICE_INDEXES = os.getenv("WEBCAM_DEVICE_INDEXES", "0")
        instance.WEBCAM_DEVICE_NAMES = os.getenv("WEBCAM_DEVICE_NAMES", "")

        # RealSense 相机配置
        instance.VISION_CAMERA_HOST = os.getenv("VISION_CAMERA_HOST", "localhost")
        instance.VISION_CAMERA_PORT = int(os.getenv("VISION_CAMERA_PORT", "12345"))
        instance.YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "models/best.pt")
        instance.SAM_MODEL_PATH = os.getenv("SAM_MODEL_PATH", "models/sam2.1_l.pt")
        instance.VISION_DEBUG_SAVE_DIR = os.getenv("VISION_DEBUG_SAVE_DIR", "pictures")

        # 手眼标定参数
        instance.VISION_ROTATION_MATRIX = cls._parse_float_list(os.getenv(
            "VISION_ROTATION_MATRIX",
            "0.00215684,0.97503835,0.22202606,-0.99995231,-0.0000119,0.00976617,0.00952503,-0.22203654,0.97499182"
        ))
        instance.VISION_TRANSLATION_VECTOR = cls._parse_float_list(os.getenv(
            "VISION_TRANSLATION_VECTOR", "-0.10273135,0.03312807,-0.07214614"
        ))
        instance.VISION_GRIPPER_OFFSET = cls._parse_float_list(os.getenv(
            "VISION_GRIPPER_OFFSET", "3.146,0.0,3.128"
        ))
        instance.VISION_DEFAULT_CONFIDENCE = float(os.getenv("VISION_DEFAULT_CONFIDENCE", "0.7"))
        instance.VISION_DEFAULT_VELOCITY = int(os.getenv("VISION_DEFAULT_VELOCITY", "15"))
        instance.VISION_DEFAULT_GRIPPER_LENGTH = float(os.getenv("VISION_DEFAULT_GRIPPER_LENGTH", "150.0"))
        instance.VISION_DEFAULT_WORKFLOW = os.getenv("VISION_DEFAULT_WORKFLOW", "bottle")
        instance.VISION_CAMERA_NAME = os.getenv("VISION_CAMERA_NAME", "")
        instance.VISION_PREP_OFFSET_X = float(os.getenv("VISION_PREP_OFFSET_X", "-0.07"))
        instance.VISION_GRASP_Z = float(os.getenv("VISION_GRASP_Z", "-0.24"))
        instance.VISION_GMM_COMPONENTS = int(os.getenv("VISION_GMM_COMPONENTS", "1"))
        
        # 机械臂配置
        instance.ROBOT1_IP = os.getenv("ROBOT1_IP", "192.168.3.19")
        instance.ROBOT1_PORT = int(os.getenv("ROBOT1_PORT", "8080"))
        instance.ROBOT1_INITIAL_POSE = cls._parse_float_list(os.getenv("ROBOT1_INITIAL_POSE", "-0.04844,-0.269769,-0.101888,3.109,-0.094,-1.592"))
        instance.ROBOT2_IP = os.getenv("ROBOT2_IP", "192.168.3.18")
        instance.ROBOT2_PORT = int(os.getenv("ROBOT2_PORT", "8080"))
        instance.ROBOT2_INITIAL_POSE = cls._parse_float_list(os.getenv("ROBOT2_INITIAL_POSE", "-0.053437,0.24741,-0.120801,3.114,-0.032,-2.935"))
        instance.MOVE_CONTROLLER_HOST = os.getenv("MOVE_CONTROLLER_HOST", "192.168.1.216")
        instance.MOVE_CONTROLLER_PORT = int(os.getenv("MOVE_CONTROLLER_PORT", "12345"))
        move_client_bind_port = os.getenv("MOVE_CONTROLLER_CLIENT_BIND_PORT")
        instance.MOVE_CONTROLLER_CLIENT_BIND_PORT = int(move_client_bind_port) if move_client_bind_port else None
        instance.MOVE_SPEED = int(os.getenv("MOVE_SPEED", "10"))
        instance.MOVE_VELOCITY = int(os.getenv("MOVE_VELOCITY", "10"))
        instance.MOVE_RADIUS = int(os.getenv("MOVE_RADIUS", "0"))
        instance.MOVE_CONNECT = int(os.getenv("MOVE_CONNECT", "0"))
        instance.MOVE_BLOCK = int(os.getenv("MOVE_BLOCK", "1"))
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
        instance.EXPRESSION_DISPLAY_ENABLED = os.getenv("EXPRESSION_DISPLAY_ENABLED", "false").lower() in ("true", "1", "yes")
        instance.EXPRESSION_DISPLAY_PROVIDER = os.getenv("EXPRESSION_DISPLAY_PROVIDER", "t5l_dgusii")
        instance.EXPRESSION_DISPLAY_CONFIG = os.getenv("EXPRESSION_DISPLAY_CONFIG", "")
        instance.EXPRESSION_DISPLAY_SERIAL_PORT = os.getenv("EXPRESSION_DISPLAY_SERIAL_PORT", "COM4")
        instance.EXPRESSION_DISPLAY_BAUDRATE = int(os.getenv("EXPRESSION_DISPLAY_BAUDRATE", "115200"))
        instance.EXPRESSION_DISPLAY_TIMEOUT = float(os.getenv("EXPRESSION_DISPLAY_TIMEOUT", "0.5"))
        instance.EXPRESSION_DISPLAY_WRITE_TIMEOUT = float(os.getenv("EXPRESSION_DISPLAY_WRITE_TIMEOUT", "1.0"))
        instance.EXPRESSION_DISPLAY_VP_ADDR = os.getenv("EXPRESSION_DISPLAY_VP_ADDR", "0x5602")
        instance.EXPRESSION_DISPLAY_SP_ADDR = os.getenv("EXPRESSION_DISPLAY_SP_ADDR", "0x8000")
        instance.EXPRESSION_DISPLAY_START_VALUE = os.getenv("EXPRESSION_DISPLAY_START_VALUE", "0x0000")
        instance.EXPRESSION_DISPLAY_STOP_VALUE = os.getenv("EXPRESSION_DISPLAY_STOP_VALUE", "0x0001")
        instance.EXPRESSION_DISPLAY_HIDE_VALUE = os.getenv("EXPRESSION_DISPLAY_HIDE_VALUE", "0x0002")
        instance.EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH = os.getenv("EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH", "stop")
        instance.EXPRESSION_DISPLAY_SWITCH_DELAY = float(os.getenv("EXPRESSION_DISPLAY_SWITCH_DELAY", "0.1"))
        instance.EXPRESSION_DISPLAY_UPDATE_ICON_RANGE = os.getenv("EXPRESSION_DISPLAY_UPDATE_ICON_RANGE", "true").lower() in ("true", "1", "yes")
        instance.EXPRESSION_DISPLAY_EXPRESSIONS = os.getenv(
            "EXPRESSION_DISPLAY_EXPRESSIONS",
            "happy:24:0:63,sad:27:0:63,angry:30:0:63,speechless:33:0:63,default_1:36:0:63,default_2:39:0:63",
        )
        instance.EXPRESSION_DISPLAY_CLEAR_VPS = os.getenv("EXPRESSION_DISPLAY_CLEAR_VPS", "")
        instance.EXPRESSION_DISPLAY_TEST_INTERVAL = float(os.getenv("EXPRESSION_DISPLAY_TEST_INTERVAL", "1.5"))
        instance.EXPRESSION_DISPLAY_TX_DELAY = float(os.getenv("EXPRESSION_DISPLAY_TX_DELAY", "0.05"))
        
        # WebSocket 服务器配置
        instance.WEBSOCKET_HOST = os.getenv("WEBSOCKET_HOST", "0.0.0.0")
        instance.WEBSOCKET_PORT = int(os.getenv("WEBSOCKET_PORT", "8765"))

        # MiniCPM Realtime / 聊天配置
        instance.MINICPM_GATEWAY_HOST = os.getenv("MINICPM_GATEWAY_HOST", "localhost")
        instance.MINICPM_GATEWAY_PORT = int(os.getenv("MINICPM_GATEWAY_PORT", "8006"))
        instance.MINICPM_WS_SCHEME = os.getenv("MINICPM_WS_SCHEME", "wss")
        instance.MINICPM_GATEWAY_PATH_PREFIX = os.getenv("MINICPM_GATEWAY_PATH_PREFIX", "")
        instance.MINICPM_REALTIME_PATH = os.getenv("MINICPM_REALTIME_PATH", "/v1/realtime")
        instance.MINICPM_MODEL = os.getenv("MINICPM_MODEL", "minicpm-o")
        instance.MINICPM_ASK_ENABLED = os.getenv(
            "MINICPM_ASK_ENABLED", "true").lower() in ("true", "1", "yes")
        instance.MINICPM_ASK_API_KEY = os.getenv("MINICPM_ASK_API_KEY", "")
        instance.MINICPM_ASK_BASE_URL = os.getenv("MINICPM_ASK_BASE_URL", "")
        instance.MINICPM_ASK_MODEL = os.getenv("MINICPM_ASK_MODEL", "gpt-4o-mini")
        
        # 位置配置
        instance.INITIAL_POSE = cls._parse_float_list(os.getenv("INITIAL_POSE", "-0.303379,0.274441,-0.075986,-3.081,0.137,-1.828"))
        instance.LEFT_INITIAL_POSE = cls._parse_float_list(os.getenv("LEFT_INITIAL_POSE", "-0.356,0.309,-0.186,-3.141,0,-1.89"))
        instance.RIGHT_INITIAL_POSE = cls._parse_float_list(os.getenv("RIGHT_INITIAL_POSE", "-0.372,0.221,-0.186,-3.121,0,-1.89"))
        instance.PLACE_DROP_HEIGHT = float(os.getenv("PLACE_DROP_HEIGHT", "0.06"))
        instance.PLACE_ABOVE = cls._parse_float_list(os.getenv("PLACE_ABOVE", "0.0637,-0.07351,-0.4182,3.15,0,1.617"))
        instance.PLACE_POS2 = cls._parse_float_list(os.getenv("PLACE_POS2", "0.285488,-0.256408,-0.090654,3.14,0,1.5"))

        # 枪头更换位置配置
        instance.GUN1_POSITIONS = {
            "1shang": cls._parse_float_list(os.getenv("GUN1_1SHANG", "")),
            "1xia":   cls._parse_float_list(os.getenv("GUN1_1XIA", "")),
            "1zhong": cls._parse_float_list(os.getenv("GUN1_1ZHONG", "")),
        }
        instance.GUN2_POSITIONS = {
            "2shang": cls._parse_float_list(os.getenv("GUN2_2SHANG", "")),
            "2xia":   cls._parse_float_list(os.getenv("GUN2_2XIA", "")),
            "2zhong": cls._parse_float_list(os.getenv("GUN2_2ZHONG", "")),
        }

        cls._loaded = True
        return instance

    @classmethod
    def get_instance(cls) -> 'Config':
        """获取单例实例，如果未加载则先加载"""
        if not cls._loaded:
            cls.load()
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def is_api_key_set(cls) -> bool:
        """检查默认 LLM provider 是否已配置。"""
        instance = cls.get_instance()
        provider = (
            instance.LLM_DEFAULT_PROVIDER
            or "openai"
        ).lower()
        if provider == "minicpm":
            return bool(instance.MINICPM_GATEWAY_HOST)
        key = instance.OPENAI_API_KEY
        return bool(key and key != "your_openai_key_here")

    @classmethod
    def get_skill_library_path(cls) -> Path:
        """获取技能库文件的绝对路径"""
        instance = cls.get_instance()
        path = Path(instance.SKILL_LIBRARY_PATH)

        # 如果是相对路径，转换为相对于项目根目录
        if not path.is_absolute():
            _src_dir = Path(__file__).parent.parent.parent
            path = _src_dir / instance.SKILL_LIBRARY_PATH

        return path

    @classmethod
    def _parse_float_list(cls, value: str) -> list:
        """解析逗号分隔的浮点数列表"""
        if not value:
            return []
        try:
            return [float(x.strip()) for x in value.split(",")]
        except (ValueError, AttributeError):
            return []


    @classmethod
    def get_robot1_config(cls) -> dict:
        """获取 Robot1 配置"""
        instance = cls.get_instance()
        return {
            "ip": instance.ROBOT1_IP,
            "port": instance.ROBOT1_PORT,
            "initial_pose": instance.ROBOT1_INITIAL_POSE
        }

    @classmethod
    def get_robot2_config(cls) -> dict:
        """获取 Robot2 配置"""
        instance = cls.get_instance()
        return {
            "ip": instance.ROBOT2_IP,
            "port": instance.ROBOT2_PORT,
            "initial_pose": instance.ROBOT2_INITIAL_POSE
        }

    @classmethod
    def get_move_config(cls) -> dict:
        """获取机械臂移动配置"""
        instance = cls.get_instance()
        return {
            "velocity": instance.MOVE_VELOCITY,
            "radius": instance.MOVE_RADIUS,
            "connect": instance.MOVE_CONNECT,
            "block": instance.MOVE_BLOCK
        }

    @classmethod
    def get_move_controller_config(cls) -> dict:
        """获取移动控制器TCP连接配置"""
        instance = cls.get_instance()
        return {
            "host": instance.MOVE_CONTROLLER_HOST,
            "port": instance.MOVE_CONTROLLER_PORT,
            "client_bind_port": instance.MOVE_CONTROLLER_CLIENT_BIND_PORT
        }

    @classmethod
    def get_gripper_config(cls) -> dict:
        """获取夹爪配置"""
        instance = cls.get_instance()
        return {
            "pick": {
                "speed": instance.GRIPPER_PICK_SPEED,
                "force": instance.GRIPPER_PICK_FORCE,
                "timeout": instance.GRIPPER_PICK_TIMEOUT
            },
            "release": {
                "speed": instance.GRIPPER_RELEASE_SPEED,
                "timeout": instance.GRIPPER_RELEASE_TIMEOUT
            }
        }

    @classmethod
    def get_body_motor_config(cls) -> dict:
        """获取身体控制器（ModbusMotor）配置"""
        instance = cls.get_instance()
        return {
            "port": instance.BODY_SERIAL_PORT,
            "baudrate": instance.BODY_BAUDRATE,
            "slave_id": instance.BODY_SLAVE_ID,
            "timeout": instance.BODY_TIMEOUT
        }

    @classmethod
    def get_kuaihuanshou_config(cls) -> dict:
        """获取快换手配置"""
        instance = cls.get_instance()
        return {
            "port": instance.KUAIHUANSHOU_SERIAL_PORT,
            "baudrate": instance.KUAIHUANSHOU_BAUDRATE,
            "timeout": instance.KUAIHUANSHOU_TIMEOUT
        }

    @classmethod
    def get_adp_config(cls) -> dict:
        """获取 ADP 吸液枪配置"""
        instance = cls.get_instance()
        return {
            "port": instance.ADP_SERIAL_PORT,
            "baudrate": instance.ADP_BAUDRATE,
            "timeout": instance.ADP_TIMEOUT,
            "max_retries": instance.ADP_MAX_RETRIES
        }

    @classmethod
    def get_relay_config(cls) -> dict:
        """获取继电器控制器配置"""
        instance = cls.get_instance()
        return {
            "port": instance.RELAY_SERIAL_PORT,
            "baudrate": instance.RELAY_BAUDRATE,
            "timeout": instance.RELAY_TIMEOUT
        }

    @classmethod
    def get_expression_display_config(cls) -> dict:
        """获取表情屏配置（不会初始化串口）。"""
        instance = cls.get_instance()
        config_path = instance.EXPRESSION_DISPLAY_CONFIG
        if config_path:
            path = Path(config_path)
            if not path.is_absolute():
                path = Path(__file__).parent.parent.parent / path
            config_path = str(path)
        return {
            "enabled": instance.EXPRESSION_DISPLAY_ENABLED,
            "provider": instance.EXPRESSION_DISPLAY_PROVIDER,
            "config_path": config_path,
            "port": instance.EXPRESSION_DISPLAY_SERIAL_PORT,
            "baudrate": instance.EXPRESSION_DISPLAY_BAUDRATE,
            "timeout": instance.EXPRESSION_DISPLAY_TIMEOUT,
            "write_timeout": instance.EXPRESSION_DISPLAY_WRITE_TIMEOUT,
            "vp_addr": instance.EXPRESSION_DISPLAY_VP_ADDR,
            "sp_addr": instance.EXPRESSION_DISPLAY_SP_ADDR,
            "start_value": instance.EXPRESSION_DISPLAY_START_VALUE,
            "stop_value": instance.EXPRESSION_DISPLAY_STOP_VALUE,
            "hide_value": instance.EXPRESSION_DISPLAY_HIDE_VALUE,
            "clear_before_switch": instance.EXPRESSION_DISPLAY_CLEAR_BEFORE_SWITCH,
            "switch_delay": instance.EXPRESSION_DISPLAY_SWITCH_DELAY,
            "update_icon_range": instance.EXPRESSION_DISPLAY_UPDATE_ICON_RANGE,
            "expressions": instance.EXPRESSION_DISPLAY_EXPRESSIONS,
            "clear_vps": instance.EXPRESSION_DISPLAY_CLEAR_VPS,
            "test_interval": instance.EXPRESSION_DISPLAY_TEST_INTERVAL,
            "tx_delay": instance.EXPRESSION_DISPLAY_TX_DELAY,
        }

    @classmethod
    def get_pwm_neck_config(cls) -> dict:
        """获取 PWM 颈部舵机配置"""
        instance = cls.get_instance()
        return {
            "port": instance.PWM_NECK_SERIAL_PORT,
            "baudrate": instance.PWM_NECK_BAUDRATE,
            "horizontal": {
                "servo_id": instance.PWM_NECK_H_SERVO_ID,
                "initial_pwm": instance.PWM_NECK_H_INITIAL_PWM,
                "pwm_min": instance.PWM_NECK_H_PWM_MIN,
                "pwm_max": instance.PWM_NECK_H_PWM_MAX,
                "default_time": instance.PWM_NECK_H_DEFAULT_TIME,
            },
            "vertical": {
                "servo_id": instance.PWM_NECK_V_SERVO_ID,
                "initial_pwm": instance.PWM_NECK_V_INITIAL_PWM,
                "pwm_min": instance.PWM_NECK_V_PWM_MIN,
                "pwm_max": instance.PWM_NECK_V_PWM_MAX,
                "default_time": instance.PWM_NECK_V_DEFAULT_TIME,
            },
        }

    @classmethod
    def get_vision_config(cls) -> dict:
        """获取视觉系统配置"""
        instance = cls.get_instance()
        return {
            "camera_provider": instance.CAMERA_PROVIDER,
            "camera_sn": instance.REALSENSE_DEVICE_SN,
            "webcam_indexes": instance.WEBCAM_DEVICE_INDEXES,
            "camera_host": instance.VISION_CAMERA_HOST,
            "camera_port": instance.VISION_CAMERA_PORT,
            "yolo_model_path": instance.YOLO_MODEL_PATH,
            "sam_model_path": instance.SAM_MODEL_PATH,
            "debug_save_dir": instance.VISION_DEBUG_SAVE_DIR
        }

    @classmethod
    def get_vision_calibration(cls) -> dict:
        """获取手眼标定参数（相机→末端变换）"""
        instance = cls.get_instance()
        mat_flat = instance.VISION_ROTATION_MATRIX or [1, 0, 0, 0, 1, 0, 0, 0, 1]
        rotation_matrix = [
            mat_flat[0:3],
            mat_flat[3:6],
            mat_flat[6:9],
        ]
        return {
            "rotation_matrix": rotation_matrix,
            "translation_vector": instance.VISION_TRANSLATION_VECTOR or [0.0, 0.0, 0.0],
            "gripper_offset": instance.VISION_GRIPPER_OFFSET or [3.146, 0.0, 3.128],
        }

    @classmethod
    def get_websocket_config(cls) -> dict:
        """获取 WebSocket 服务器配置"""
        instance = cls.get_instance()
        return {
            "host": instance.WEBSOCKET_HOST,
            "port": instance.WEBSOCKET_PORT
        }

    @classmethod
    def get_minicpm_config(cls) -> dict:
        """获取 MiniCPM Realtime / 聊天配置"""
        instance = cls.get_instance()
        return {
            "gateway_host": instance.MINICPM_GATEWAY_HOST,
            "gateway_port": instance.MINICPM_GATEWAY_PORT,
            "ws_scheme": instance.MINICPM_WS_SCHEME,
            "gateway_path_prefix": instance.MINICPM_GATEWAY_PATH_PREFIX,
            "realtime_path": instance.MINICPM_REALTIME_PATH,
            "ask_enabled": instance.MINICPM_ASK_ENABLED,
            "ask_api_key": instance.MINICPM_ASK_API_KEY or instance.OPENAI_API_KEY,
            "ask_base_url": instance.MINICPM_ASK_BASE_URL or instance.OPENAI_BASE_URL,
            "ask_model": instance.MINICPM_ASK_MODEL,
        }

    @classmethod
    def get_llm_config(cls) -> dict:
        """获取 LLM 能力层配置摘要。"""
        instance = cls.get_instance()
        return {
            "default_provider": instance.LLM_DEFAULT_PROVIDER,
            "supported_providers": ["openai", "deepseek", "dashscope", "minicpm"],
            "openai_model": instance.OPENAI_MODEL,
            "openai_base_url": instance.OPENAI_BASE_URL,
            "minicpm_model": instance.MINICPM_MODEL,
            "minicpm_ws_scheme": instance.MINICPM_WS_SCHEME,
            "minicpm_realtime_path": instance.MINICPM_REALTIME_PATH,
            "timeout_s": instance.LLM_REQUEST_TIMEOUT_S,
        }

    @classmethod
    def get_voice_interaction_config(cls) -> dict:
        """获取唤醒后语音会话配置。"""
        instance = cls.get_instance()
        return {
            "session_timeout_s": instance.VOICE_SESSION_TIMEOUT_S,
            "speech_startup_wait_timeout_s": instance.VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S,
            "auto_execute_command": instance.VOICE_AUTO_EXECUTE_COMMAND,
            "tts_enabled": instance.VOICE_TTS_ENABLED,
            "speech_input_enabled": instance.VOICE_INPUT_ENABLED,
            "wake_word_enabled": instance.VOICE_INPUT_ENABLED,
            "asr_enabled": instance.VOICE_INPUT_ENABLED,
            "audio_sample_rate": instance.VOICE_AUDIO_SAMPLE_RATE,
            "audio_channels": instance.VOICE_AUDIO_CHANNELS,
            "audio_block_ms": instance.VOICE_AUDIO_BLOCK_MS,
            "audio_queue_size": instance.VOICE_AUDIO_QUEUE_SIZE,
            "audio_latency": instance.VOICE_AUDIO_LATENCY,
            "audio_device": instance.VOICE_AUDIO_DEVICE,
            "audio_show_status": instance.VOICE_AUDIO_SHOW_STATUS,
            "vad_model": instance.VOICE_VAD_MODEL,
            "vad_chunk_ms": instance.VOICE_VAD_CHUNK_MS,
            "min_utterance_ms": instance.VOICE_MIN_UTTERANCE_MS,
            "max_utterance_ms": instance.VOICE_MAX_UTTERANCE_MS,
            "end_silence_ms": instance.VOICE_END_SILENCE_MS,
            "listening_timeout_s": instance.VOICE_LISTENING_TIMEOUT_S,
            "wake_cooldown_s": instance.VOICE_WAKE_COOLDOWN_S,
            "silence_rms_threshold": instance.VOICE_SILENCE_RMS_THRESHOLD,
            "suppress_model_output": instance.VOICE_SUPPRESS_MODEL_OUTPUT,
            "show_asr_timing": instance.VOICE_SHOW_ASR_TIMING,
            "asr_model": instance.VOICE_ASR_MODEL,
            "asr_punc_model": instance.VOICE_ASR_PUNC_MODEL,
            "asr_device": instance.VOICE_ASR_DEVICE,
            "asr_batch_size_s": instance.VOICE_ASR_BATCH_SIZE_S,
            "wake_engine": instance.VOICE_WAKE_ENGINE,
            "wake_auto_trigger": instance.VOICE_WAKE_AUTO_TRIGGER,
            "kws_encoder": instance.VOICE_KWS_ENCODER,
            "kws_decoder": instance.VOICE_KWS_DECODER,
            "kws_joiner": instance.VOICE_KWS_JOINER,
            "kws_tokens": instance.VOICE_KWS_TOKENS,
            "kws_keywords_file": instance.VOICE_KWS_KEYWORDS_FILE,
            "kws_provider": instance.VOICE_KWS_PROVIDER,
            "kws_threshold": instance.VOICE_KWS_THRESHOLD,
            "kws_score": instance.VOICE_KWS_SCORE,
            "kws_num_threads": instance.VOICE_KWS_NUM_THREADS,
            "kws_max_active_paths": instance.VOICE_KWS_MAX_ACTIVE_PATHS,
            "openwakeword_model_paths": instance.VOICE_OPENWAKEWORD_MODEL_PATHS,
            "openwakeword_threshold": instance.VOICE_OPENWAKEWORD_THRESHOLD,
        }
    
    @classmethod
    def reset(cls):
        """重置配置（用于测试）"""
        cls._instance = None
        cls._loaded = False
