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
    MODEL_PROVIDER: str = "openai"


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

    # MiniCPM 聊天代理配置
    MINICPM_GATEWAY_HOST: str = "localhost"
    MINICPM_GATEWAY_PORT: int = 8006
    MINICPM_GATEWAY_SCHEME: str = "https"
    MINICPM_GATEWAY_PATH_PREFIX: str = ""
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
        instance.MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openai")
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
        legacy_camera_matrix = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_CAMERA_MATRIX",
            default_camera_matrix,
        ))
        legacy_camera_resolution = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_CAMERA_MATRIX_RESOLUTION",
            "1920,1080",
        ))
        legacy_dist_coeffs = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_DIST_COEFFS",
            "0,0,0,0,0",
        ))
        instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX",
            "",
        )) or legacy_camera_matrix
        instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX",
            "",
        )) or legacy_camera_matrix
        instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION",
            "",
        )) or legacy_camera_resolution
        instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION",
            "",
        )) or legacy_camera_resolution
        instance.VISION_RELOCALIZATION_LEFT_DIST_COEFFS = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_LEFT_DIST_COEFFS",
            "",
        )) or legacy_dist_coeffs
        instance.VISION_RELOCALIZATION_RIGHT_DIST_COEFFS = cls._parse_float_list(os.getenv(
            "VISION_RELOCALIZATION_RIGHT_DIST_COEFFS",
            "",
        )) or legacy_dist_coeffs
        instance.VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH = float(os.getenv(
            "VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH",
            os.getenv("VISION_RELOCALIZATION_MARKER_WIDTH", "0.158"),
        ))
        instance.VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT = float(os.getenv(
            "VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT",
            os.getenv("VISION_RELOCALIZATION_MARKER_HEIGHT", "0.158"),
        ))
        instance.VISION_RELOCALIZATION_POSE_ROTATION_TYPE = os.getenv("VISION_RELOCALIZATION_POSE_ROTATION_TYPE", "rpy")
        instance.VISION_RELOCALIZATION_POSE_ANGLE_UNIT = os.getenv("VISION_RELOCALIZATION_POSE_ANGLE_UNIT", "rad")
        default_t_e_c = cls._matrix4_from_rt(instance.VISION_ROTATION_MATRIX, instance.VISION_TRANSLATION_VECTOR)
        instance.VISION_RELOCALIZATION_LEFT_T_E_C = cls._parse_matrix4(
            os.getenv("VISION_RELOCALIZATION_LEFT_T_E_C", ""),
            default_t_e_c,
        )
        instance.VISION_RELOCALIZATION_RIGHT_T_E_C = cls._parse_matrix4(
            os.getenv("VISION_RELOCALIZATION_RIGHT_T_E_C", ""),
            default_t_e_c,
        )
        instance.VISION_RELOCALIZATION_MODE = os.getenv("VISION_RELOCALIZATION_MODE", "planar")
        instance.VISION_RELOCALIZATION_PLANAR_CONSTRAINT = os.getenv("VISION_RELOCALIZATION_PLANAR_CONSTRAINT", "none")
        instance.VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES = os.getenv(
            "VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES",
            "true",
        ).lower() in ("true", "1", "yes")
        instance.VISION_RELOCALIZATION_DEBUG_DIR = os.getenv(
            "VISION_RELOCALIZATION_DEBUG_DIR",
            "data/vision_stations/debug",
        )
        
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

        # MiniCPM 聊天代理配置
        instance.MINICPM_GATEWAY_HOST = os.getenv("MINICPM_GATEWAY_HOST", "localhost")
        instance.MINICPM_GATEWAY_PORT = int(os.getenv("MINICPM_GATEWAY_PORT", "8006"))
        instance.MINICPM_GATEWAY_SCHEME = os.getenv("MINICPM_GATEWAY_SCHEME", "https")
        instance.MINICPM_GATEWAY_PATH_PREFIX = os.getenv("MINICPM_GATEWAY_PATH_PREFIX", "")
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
        """检查 OpenAI API Key 是否已配置"""
        key = cls.get_instance().OPENAI_API_KEY
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
        return [values[i:i + 4] for i in range(0, 16, 4)]

    @classmethod
    def _matrix3_from_flat(cls, values: list) -> list:
        if len(values or []) == 9:
            return [
                values[0:3],
                values[3:6],
                values[6:9],
            ]
        return [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]


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
    def get_vision_relocalization_config(cls, arm: str | None = None) -> dict:
        """获取视觉重定位固定参数。"""
        instance = cls.get_instance()
        arm_text = str(arm or "").strip().lower()
        is_right = arm_text in {"right", "r", "robot2", "r2", "2", "右", "右臂"}
        camera_name = (
            instance.VISION_RELOCALIZATION_RIGHT_CAMERA_NAME
            if is_right
            else instance.VISION_RELOCALIZATION_LEFT_CAMERA_NAME
        )
        t_e_c = (
            instance.VISION_RELOCALIZATION_RIGHT_T_E_C
            if is_right
            else instance.VISION_RELOCALIZATION_LEFT_T_E_C
        )
        camera_values = (
            instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX
            if is_right
            else instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX
        ) or []
        camera_matrix = cls._matrix3_from_flat(camera_values)
        camera_resolution = (
            instance.VISION_RELOCALIZATION_RIGHT_CAMERA_MATRIX_RESOLUTION
            if is_right
            else instance.VISION_RELOCALIZATION_LEFT_CAMERA_MATRIX_RESOLUTION
        )
        if len(camera_resolution or []) != 2:
            camera_resolution = None
        dist_coeffs = (
            instance.VISION_RELOCALIZATION_RIGHT_DIST_COEFFS
            if is_right
            else instance.VISION_RELOCALIZATION_LEFT_DIST_COEFFS
        )
        return {
            "stations_file": instance.VISION_RELOCALIZATION_STATIONS_FILE,
            "camera_name": camera_name,
            "camera_matrix": camera_matrix,
            "camera_matrix_resolution": camera_resolution,
            "dist_coeffs": dist_coeffs or [0, 0, 0, 0, 0],
            "marker": {
                "width": instance.VISION_RELOCALIZATION_DEFAULT_MARKER_WIDTH,
                "height": instance.VISION_RELOCALIZATION_DEFAULT_MARKER_HEIGHT,
            },
            "pose_rotation_type": instance.VISION_RELOCALIZATION_POSE_ROTATION_TYPE,
            "pose_angle_unit": instance.VISION_RELOCALIZATION_POSE_ANGLE_UNIT,
            "T_E_C": t_e_c,
            "mode": instance.VISION_RELOCALIZATION_MODE,
            "planar_constraint": instance.VISION_RELOCALIZATION_PLANAR_CONSTRAINT,
            "save_debug_images": instance.VISION_RELOCALIZATION_SAVE_DEBUG_IMAGES,
            "debug_dir": instance.VISION_RELOCALIZATION_DEBUG_DIR,
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
    def get_minicpm_proxy_config(cls) -> dict:
        """获取 MiniCPM 聊天代理配置"""
        instance = cls.get_instance()
        return {
            "gateway_host": instance.MINICPM_GATEWAY_HOST,
            "gateway_port": instance.MINICPM_GATEWAY_PORT,
            "gateway_scheme": instance.MINICPM_GATEWAY_SCHEME,
            "gateway_path_prefix": instance.MINICPM_GATEWAY_PATH_PREFIX,
            "ask_enabled": instance.MINICPM_ASK_ENABLED,
            "ask_api_key": instance.MINICPM_ASK_API_KEY or instance.OPENAI_API_KEY,
            "ask_base_url": instance.MINICPM_ASK_BASE_URL or instance.OPENAI_BASE_URL,
            "ask_model": instance.MINICPM_ASK_MODEL,
        }
    
    @classmethod
    def reset(cls):
        """重置配置（用于测试）"""
        cls._instance = None
        cls._loaded = False
