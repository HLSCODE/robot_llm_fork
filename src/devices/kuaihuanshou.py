import serial
import time
import struct

# 延迟加载配置
_kuaihuanshou_config_cache = None

def _get_kuaihuanshou_config():
    """延迟加载配置，避免循环导入"""
    global _kuaihuanshou_config_cache
    if _kuaihuanshou_config_cache is None:
        try:
            from ..core.config_loader import Config
            config = Config.get_instance()
            _kuaihuanshou_config_cache = config.get_kuaihuanshou_config()
        except Exception as e:
            print(f"加载快换手配置失败：{e}，使用默认值")
            _kuaihuanshou_config_cache = {
                "port": "/dev/hand",
                "baudrate": 115200,
                "timeout": 3
            }
    return _kuaihuanshou_config_cache

class Kuaihuanshou:
    def __init__(self, port=None, baudrate=None, timeout=None):
        """初始化快换手控制器
        
        Args:
            port (str): 串口号，默认从 config.env 读取
            baudrate (int): 波特率，默认从 config.env 读取
            timeout (int): 超时时间，默认从 config.env 读取
        """
        # 如果未提供参数，从配置加载器读取
        if port is None or baudrate is None or timeout is None:
            config = _get_kuaihuanshou_config()
            if port is None:
                port = config.get("port", "/dev/hand")
            if baudrate is None:
                baudrate = config.get("baudrate", 115200)
            if timeout is None:
                timeout = config.get("timeout", 3)
        
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = self._connect()
        
        # 定义命令字典
        self.commands = {
            'close': bytes([0x53, 0x26, 0x01, 0x01, 0x01]),
            'open': bytes([0x53, 0x26, 0x01, 0x01, 0x02]),
            'status': bytes([0x53, 0x26, 0x02, 0x01, 0x01]),
            'temp': bytes([0x53, 0x26, 0x03, 0x01, 0x01]),
            'power_on': bytes([0x53, 0x26, 0x04, 0x01, 0x01]),
            'power_off': bytes([0x53, 0x26, 0x04, 0x01, 0x02]),
            'power_status': bytes([0x53, 0x26, 0x05, 0x01, 0x01])
        }

    def _connect(self):
        """内部方法：建立串口连接"""
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            print(f"串口 {self.port} 初始化成功")
            return ser
        except Exception as e:
            print(f"串口初始化失败: {str(e)}")
            return None

    @staticmethod
    def _crc16(data):
        """内部方法：计算CRC16校验值"""
        crc = 0xFFFF
        for byte in data:
            if isinstance(byte, str):
                byte = ord(byte)
            crc ^= byte
            for _ in range(8):
                if (crc & 0x0001) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc

    def _create_command(self, switch_command):
        """内部方法：创建命令包"""
        crc = self._crc16(switch_command)
        crc_bytes = struct.pack('<H', crc)
        return switch_command + crc_bytes

    def send_command(self, command_type):
        """发送命令并获取响应
        
        Args:
            command_type (str): 命令类型，可选值：
                'close': 关闭快换手
                'open': 打开快换手
                'status': 获取状态
                'temp': 获取温度
                'power_on': 开启电源
                'power_off': 关闭电源
                'power_status': 获取电源状态
        
        Returns:
            str/bool/int: 根据命令类型返回不同的结果
        """
        if command_type not in self.commands:
            print(f"未知的命令类型: {command_type}")
            return "error"

        try:
            command = self._create_command(self.commands[command_type])
            self.ser.write(command)
            response = self.ser.read(size=len(command))

            if not response or len(response) < 5:
                print(f"警告: 接收到的响应数据不完整 - 长度: {len(response) if response else 0}")
                return "error"

            print(f"收到响应: {[hex(b) for b in response]}")

            if command_type == 'temp':
                return int(response[4])
            elif command_type in ['close', 'open', 'power_on', 'power_off']:
                return response[:5] == self.commands[command_type]
            elif command_type == 'status':
                if response[4] == 1:
                    return "locked"
                elif response[4] == 2:
                    return "unlocked"
                else:
                    return "unknown"
            else:  # power_status
                if response[4] == 1:
                    return "on"
                elif response[4] == 2:
                    return "off"
                else:
                    return "error"

        except Exception as e:
            print(f"控制命令执行出错: {str(e)}")
            return "error"
        finally:
            print(f"命令 {command_type} 执行完成")

    def close(self):
        """关闭串口连接"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print(f"串口 {self.port} 已关闭")

if __name__ == "__main__":
    # 测试快换手
    try:
        print("\n开始测试快换手功能...")
        khs = Kuaihuanshou(port='COM4')
        
        print("状态:", khs.send_command('status'))
        time.sleep(1)
        print("打开:", khs.send_command('open'))
        time.sleep(1)
        print("关闭:", khs.send_command('close'))
        
    except Exception as e:
        print(f"快换手测试出错: {str(e)}")
    finally:
        if 'khs' in locals():
            khs.close()
        print("测试完成") 