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
    
    # 机械臂配置
    ROBOT1_IP: str = "192.168.3.18"
    ROBOT1_PORT: int = 8080
    ROBOT1_INITIAL_POSE: list = None
    ROBOT2_IP: str = "192.168.3.19"
    ROBOT2_PORT: int = 8080
    ROBOT2_INITIAL_POSE: list = None
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
    BODY_SERIAL_PORT: str = "/dev/body"
    BODY_BAUDRATE: int = 115200
    BODY_SLAVE_ID: int = 1
    BODY_TIMEOUT: int = 1
    KUAIHUANSHOU_SERIAL_PORT: str = "/dev/hand"
    KUAIHUANSHOU_BAUDRATE: int = 115200
    KUAIHUANSHOU_TIMEOUT: int = 3
    ADP_SERIAL_PORT: str = "/dev/hand"
    ADP_BAUDRATE: int = 115200
    ADP_TIMEOUT: int = 5
    ADP_MAX_RETRIES: int = 3
    RELAY_SERIAL_PORT: str = "/dev/power"
    RELAY_BAUDRATE: int = 38400
    RELAY_TIMEOUT: int = 1
    
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
        
        # 机械臂配置
        instance.ROBOT1_IP = os.getenv("ROBOT1_IP", "192.168.3.18")
        instance.ROBOT1_PORT = int(os.getenv("ROBOT1_PORT", "8080"))
        instance.ROBOT1_INITIAL_POSE = cls._parse_float_list(os.getenv("ROBOT1_INITIAL_POSE", "-0.04844,-0.269769,-0.101888,3.109,-0.094,-1.592"))
        instance.ROBOT2_IP = os.getenv("ROBOT2_IP", "192.168.3.19")
        instance.ROBOT2_PORT = int(os.getenv("ROBOT2_PORT", "8080"))
        instance.ROBOT2_INITIAL_POSE = cls._parse_float_list(os.getenv("ROBOT2_INITIAL_POSE", "-0.053437,0.24741,-0.120801,3.114,-0.032,-2.935"))
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
        
        # 串口设备配置
        instance.BODY_SERIAL_PORT = os.getenv("BODY_SERIAL_PORT", "/dev/body")
        instance.BODY_BAUDRATE = int(os.getenv("BODY_BAUDRATE", "115200"))
        instance.BODY_SLAVE_ID = int(os.getenv("BODY_SLAVE_ID", "1"))
        instance.BODY_TIMEOUT = int(os.getenv("BODY_TIMEOUT", "1"))
        instance.KUAIHUANSHOU_SERIAL_PORT = os.getenv("KUAIHUANSHOU_SERIAL_PORT", "/dev/hand")
        instance.KUAIHUANSHOU_BAUDRATE = int(os.getenv("KUAIHUANSHOU_BAUDRATE", "115200"))
        instance.KUAIHUANSHOU_TIMEOUT = int(os.getenv("KUAIHUANSHOU_TIMEOUT", "3"))
        instance.ADP_SERIAL_PORT = os.getenv("ADP_SERIAL_PORT", "/dev/hand")
        instance.ADP_BAUDRATE = int(os.getenv("ADP_BAUDRATE", "115200"))
        instance.ADP_TIMEOUT = int(os.getenv("ADP_TIMEOUT", "5"))
        instance.ADP_MAX_RETRIES = int(os.getenv("ADP_MAX_RETRIES", "3"))
        instance.RELAY_SERIAL_PORT = os.getenv("RELAY_SERIAL_PORT", "/dev/power")
        instance.RELAY_BAUDRATE = int(os.getenv("RELAY_BAUDRATE", "38400"))
        instance.RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", "1"))
        
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
