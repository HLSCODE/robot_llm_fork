"""
数据采集配置
从 config.env 加载配置项
"""
import os
from pathlib import Path
from dotenv import load_dotenv


class DataCollectionConfig:
    """
    数据采集配置类
    从 config.env 或环境变量加载
    """
    # 默认值
    FPS: int = 30
    CAMERA_INDEX: int = 0
    SAVE_PATH: str = "data/demos"
    
    def __init__(self):
        # 加载配置文件
        env_path = Path(__file__).parent.parent.parent.parent / "config.env"
        if env_path.exists():
            load_dotenv(env_path)
        
        # 从环境变量读取（如果存在）
        fps_str = os.getenv("DATA_COLLECTION_FPS", "30")
        self.FPS = int(fps_str)
        
        camera_index_str = os.getenv("DATA_COLLECTION_CAMERA_INDEX", "0")
        self.CAMERA_INDEX = int(camera_index_str)
        
        self.SAVE_PATH = os.getenv("DATA_COLLECTION_SAVE_PATH", "data/demos")
    
    def __repr__(self):
        return f"DataCollectionConfig(FPS={self.FPS}, CAMERA_INDEX={self.CAMERA_INDEX}, SAVE_PATH='{self.SAVE_PATH}')"