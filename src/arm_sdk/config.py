import os
import json
from typing import Optional, Dict, Any

# 从统一配置加载器获取配置
_config_cache: Optional[Dict[str, Any]] = None

def _get_config():
    """延迟加载配置，避免循环导入"""
    global _config_cache
    if _config_cache is None:
        try:
            from ..core.config_loader import Config
            config = Config.get_instance()
            _config_cache = {
                'robot1': config.get_robot1_config(),
                'robot2': config.get_robot2_config(),
                'move': config.get_move_config(),
                'gripper': config.get_gripper_config(),
                'max_attempts': config.MAX_ATTEMPTS,
                'move_speed': config.MOVE_SPEED,
            }
        except Exception as e:
            print(f"加载配置失败：{e}，使用默认值")
            _config_cache = {
                'robot1': {"ip": "192.168.3.18", "port": 8080, "initial_pose": [-0.04844, -0.269769, -0.101888, 3.109, -0.094, -1.592]},
                'robot2': {"ip": "192.168.3.19", "port": 8080, "initial_pose": [-0.053437, 0.24741, -0.120801, 3.114, -0.032, -2.935]},
                'move': {"velocity": 10, "radius": 0, "connect": 0, "block": 1},
                'gripper': {"pick": {"speed": 200, "force": 1000, "timeout": 3}, "release": {"speed": 100, "timeout": 3}},
                'max_attempts': 5,
                'move_speed': 10
            }
    return _config_cache

# 配置文件路径
POSITION_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'position_config.txt')

def update_offset(current_x, current_y, current_angle=0):
    """
    更新当前偏移量并保存到文件
    :param current_x: 当前x位置（厘米）
    :param current_y: 当前y位置（厘米）
    :param current_angle: 当前角度
    """
    try:
        # 读取现有配置
        with open(POSITION_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 更新偏移量
        config['current_offset']['x'] = current_x
        config['current_offset']['y'] = current_y
        config['current_offset']['angle'] = current_angle
        
        # 写回文件
        with open(POSITION_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
            
    except Exception as e:
        print(f"更新位置配置文件失败: {str(e)}")

# 初始位置
INITIAL_POSE = [-0.303379, 0.274441, -0.075986, -3.081, 0.137, -1.828]

# for qr code detection
LEFT_INITIAL_POSE_zero = [-0.007, 0.207, -0.1547, 3.101, 0.111, 3.144]
LEFT_INITIAL_POSE = [-0.356, 0.309, -0.186, -3.141, 0, -1.89]

RIGHT_INITIAL_POSE_zero = [0.007, 0.207, -0.1547, 3.101, 0.111, 3.144]
RIGHT_INITIAL_POSE = [-0.372, 0.221, -0.186, -3.121, 0, -1.89]

PLACE_POSITION = {
    'drop_height': 0.06,
    'above': [63.7 / 1000, -73.51 / 1000, -418.2 / 1000, 3.15, 0, 1.617],
    'pos2': [285.488 / 1000, -256.408 / 1000, -90.654 / 1000, 3.14, 0, 1.5],
    'pos1': []
}

# 夹爪配置
GRIPPER_CONFIG = None  # 从配置加载器动态获取

# 移动速度配置
MOVE_SPEED = None  # 从配置加载器动态获取

# 最大尝试次数
MAX_ATTEMPTS = None  # 从配置加载器动态获取

# 机械臂配置
ROBOT1_CONFIG = None  # 从配置加载器动态获取
ROBOT2_CONFIG = None  # 从配置加载器动态获取

# 机械臂移动配置
MOVE_CONFIG = None  # 从配置加载器动态获取

# 枪头更换位置配置（从 config.env 读取）
GUN1_POSITIONS = None  # 从配置加载器动态获取
GUN2_POSITIONS = None  # 从配置加载器动态获取

# 其他位置配置
pos_beizishangmian = [-0.207854, -0.421895, -0.230171, -3.090000, 0.005000, -2.858000]
pos_beizilimian = [-0.207839, -0.421905, -0.273585, -3.090000, 0.005000, -2.858000]
pos_beizi_temp = [-0.105176, -0.251429, -0.279415, 3.139000, 0.017000, -2.902000]
pos_zhuye = [-0.058275, -0.412350, -0.153650, -2.934000, 0.428000, -2.722000]
pos_zhuye_temp = [-0.065727, -0.425093, -0.081294, -3.005000, 0.142000, -2.844000]
pos_pingzi_jia = [0.068791, -0.011249, -0.488797, -3.107000, 0.000000, 1.602000]
pos_pingzi_shang = [0.068791, -0.011241, -0.423676, -3.107000, 0.000000, 1.603000]
pos_pingzi_temp1 = [0.180891, -0.010182, -0.34663, -3.106000, 0.000000, 1.602000]
pos_pingzi_temp2 = [0.188200, -0.223871, -0.246926, 3.139000, 0.029000, 1.421000]
pos_pingzi_temp3 = [0.093812, -0.378909, -0.243591, -3.114000, 0.104000, 1.649000]
pos_pingzi_fang = [0.089472, -0.389853, -0.281694, -3.090000, 0.042000, 1.586000]
pos_hengzhejia = [0.277553, -0.002143, -0.436172, 2.441000, 1.486000, -0.628000]

# ============================================================
# 延迟加载函数 - 确保配置已初始化后再使用
# ============================================================
def ensure_config_loaded():
    """确保配置已加载"""
    global GRIPPER_CONFIG, MOVE_SPEED, MAX_ATTEMPTS, ROBOT1_CONFIG, ROBOT2_CONFIG, MOVE_CONFIG, GUN1_POSITIONS, GUN2_POSITIONS
    
    # 如果已经加载过，直接返回
    if ROBOT1_CONFIG is not None:
        return
    
    config = _get_config()
    ROBOT1_CONFIG = config['robot1']
    ROBOT2_CONFIG = config['robot2']
    MOVE_CONFIG = config['move']
    GRIPPER_CONFIG = config['gripper']
    MAX_ATTEMPTS = config['max_attempts']
    MOVE_SPEED = config['move_speed']
    
    # 从 config.env 加载枪头位置
    try:
        from ..core.config_loader import Config
        cfg = Config.get_instance()
        if cfg.GUN1_POSITIONS:
            GUN1_POSITIONS = cfg.GUN1_POSITIONS
        if cfg.GUN2_POSITIONS:
            GUN2_POSITIONS = cfg.GUN2_POSITIONS
    except Exception:
        pass

# 模块导入时自动加载配置
try:
    ensure_config_loaded()
except Exception as e:
    print(f"自动加载配置失败：{e}，将使用默认值")