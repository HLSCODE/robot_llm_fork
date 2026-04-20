import serial
import time

# 延迟加载配置
_relay_config_cache = None

def _get_relay_config():
    """延迟加载配置，避免循环导入"""
    global _relay_config_cache
    if _relay_config_cache is None:
        try:
            from ..core.config_loader import Config
            config = Config.get_instance()
            _relay_config_cache = config.get_relay_config()
        except Exception as e:
            print(f"加载继电器配置失败：{e}，使用默认值")
            _relay_config_cache = {
                "port": "/dev/power",
                "baudrate": 38400,
                "timeout": 1
            }
    return _relay_config_cache

class RelayController:
    def __init__(self, port=None, baudrate=None, timeout=None):
        """初始化继电器控制器
        Args:
            port (str): 串口号，默认从 config.env 读取
            baudrate (int): 波特率，默认从 config.env 读取
            timeout (int): 超时时间，默认从 config.env 读取
        """
        # 如果未提供参数，从配置加载器读取
        if port is None or baudrate is None or timeout is None:
            config = _get_relay_config()
            if port is None:
                port = config.get("port", "/dev/power")
            if baudrate is None:
                baudrate = config.get("baudrate", 38400)
            if timeout is None:
                timeout = config.get("timeout", 1)
        
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def turn_on_relay_Y1(self):
        """打开继电器Y1"""
        command_on = b'\x01\x06\x00\x00\x00\x01\x48\x0A'
        self.ser.write(command_on)
        print("继电器 Y1 已打开")

    def turn_off_relay_Y1(self):
        """关闭继电器Y1"""
        command_off = b'\x01\x06\x00\x00\x00\x00\x89\xCA'
        self.ser.write(command_off)
        print("继电器 Y1 已关闭")

    def turn_on_relay_Y2(self):
        """打开继电器Y2"""
        command_on = b'\x01\x06\x00\x01\x00\x01\x19\xCA'
        self.ser.write(command_on)
        print("继电器 Y2 已打开")

    def turn_off_relay_Y2(self):
        """关闭继电器Y2"""
        command_off = b'\x01\x06\x00\x01\x00\x00\xD8\x0A'
        self.ser.write(command_off)
        print("继电器 Y2 已关闭")

    def close(self):
        """关闭串口连接"""
        self.ser.close()

if __name__ == "__main__":
    # 测试代码
    controller = RelayController()  # 创建控制器实例
    
    # 测试控制继电器 Y1 和 Y2
    controller.turn_on_relay_Y1()
    time.sleep(2)
    # controller.turn_off_relay_Y1()
    # time.sleep(2)

    controller.turn_on_relay_Y2()
    time.sleep(2)
    # controller.turn_off_relay_Y2()
    # time.sleep(2)

    # 关闭串口连接
    controller.close()
