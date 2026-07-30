import serial
import time

# 全局变量用于跟踪已打开的串口
_open_ports = {}

class ADP:
    def __init__(self, port, baudrate, timeout, max_retries):
        """Initialize the ADP from explicit device settings."""
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.max_retries = max_retries
        
        # 检查是否已经有实例使用了这个端口
        global _open_ports
        if port in _open_ports and _open_ports[port].is_open:
            print(f"使用已经打开的串口 {port}")
            self.ser = _open_ports[port]
        else:
            self.ser = self._init_serial()
            if self.ser:
                _open_ports[port] = self.ser

    def _init_serial(self):
        """内部方法：初始化串口连接"""
        for attempt in range(self.max_retries):
            try:
                # 如果串口已经被占用，先尝试关闭
                try:
                    temp_ser = serial.Serial(self.port)
                    temp_ser.close()
                    time.sleep(1)  # 等待串口释放
                except:
                    pass
                
                ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=self.timeout)
                print(f"串口 {self.port} 初始化成功")
                return ser
            except Exception as e:
                print(f"第 {attempt + 1} 次尝试初始化串口失败: {str(e)}")
                time.sleep(1)  # 等待一秒后重试
        
        print(f"串口 {self.port} 初始化失败，已重试 {self.max_retries} 次")
        return None

    @staticmethod
    def _cal_crc(byte_arr):
        """内部方法：计算CRC校验值"""
        itemp = 0xFFFF
        for b in byte_arr:
            itemp ^= b
            for _ in range(8):
                if itemp & 0x0001:
                    itemp = (itemp >> 1) ^ 0xA001
                else:
                    itemp >>= 1
        return itemp

    @staticmethod
    def _decimal_to_hex(decimal_number):
        """内部方法：十进制转十六进制"""
        return f"{decimal_number:04X}"

    def _create_command(self, func_code, volume=None):
        """内部方法：创建命令帧
        
        Args:
            func_code (str): 功能代码 ('n'吸液, 'p'吐液, 'G'初始化等)
            volume (int, optional): 体积（单位微升）
        """
        addr = "01"
        data = self._decimal_to_hex(volume) if volume is not None else ""
        frame = f">{addr}{func_code}{data}"
        crc_value = self._cal_crc(frame.encode('ascii'))
        crc_str = f"{crc_value:04X}"
        return frame + crc_str

    @staticmethod
    def _validate_speed(speed_ul):
        if speed_ul is None:
            return None
        speed = int(speed_ul)
        if speed <= 0 or speed > 9999:
            raise ValueError(f"ADP speed must be in 1-9999 uL/s, got {speed}")
        return speed

    def send_command(self, command_str):
        """发送命令到设备
        
        Args:
            command_str (str): 要发送的命令字符串
        
        Returns:
            bool: 命令发送是否成功
        """
        try:
            if self.ser and self.ser.is_open:
                print("发送命令:", command_str)
                self.ser.write(command_str.encode('ascii'))
                response = self.ser.read(20)
                print("响应:", response.decode('ascii', errors='replace'))
                return True
            else:
                print("串口未打开")
                return False
        except Exception as e:
            print("串口通信出错:", e)
            return False

    def initialize(self):
        """初始化空气泵"""
        command = self._create_command('G')
        return self.send_command(command)

    def set_absorb_speed(self, speed_ul):
        """设置吸液速度，单位 uL/s，范围 1-9999。"""
        speed = self._validate_speed(speed_ul)
        if speed is None:
            return True
        command = self._create_command('4', speed)
        return self.send_command(command)

    def set_dispense_speed(self, speed_ul):
        """设置吐液速度，单位 uL/s，范围 1-9999。"""
        speed = self._validate_speed(speed_ul)
        if speed is None:
            return True
        command = self._create_command('B', speed)
        return self.send_command(command)

    def absorb(self, volume):
        """吸液操作
        
        Args:
            volume (int): 吸液体积（微升）
        """
        command = self._create_command('n', volume)
        return self.send_command(command)

    def dispense(self, volume):
        """吐液操作
        
        Args:
            volume (int): 吐液体积（微升）
        """
        command = self._create_command('p', volume)
        return self.send_command(command)

    def dispense_all(self):
        """一次性吐出所有液体"""
        command_str = ">01p000061AC"  # 完全吐液的固定指令
        return self.send_command(command_str)

    def close(self):
        """关闭串口连接"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print(f"串口 {self.port} 已关闭")
