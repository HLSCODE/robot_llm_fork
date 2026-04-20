"""
移液枪初始化 (YIYEQIANG_INIT)
使用RS-485协议发送初始化命令

RS-485 协议：
- 发送: >01G6158\r\n
- 解析: > 为帧头，01 为设备 ID，G 为初始化指令，6158 为 CRC 校验码

ModBus-RTU 协议（备用）：
- 发送: 01 06 01 00 00 01 49 F6
- 寄存器地址: 0x0100，写入数值: 0x0001

使用方法：
    python YIYEQIANG_INIT.py
"""
import serial
import time
import sys


def init_tip(port='/dev/hand', baudrate=115200):
    """
    初始化枪头 - 使用RS-485协议
    
    Args:
        port: 串口号，默认/dev/hand
        baudrate: 波特率，默认115200
    
    Returns:
        bool: 是否执行成功
    """
    try:
        # 打开串口
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=3
        )
        
        print(f"串口 {port} 已打开")
        
        # RS-485协议：>01G6158\r\n
        # > 为帧头，01 为设备 ID，G 为初始化指令，6158 为 CRC 校验码
        command_str = ">01G6158\r\n"
        
        print(f"发送命令(RS-485): {command_str.strip()}")
        ser.write(command_str.encode('ascii'))
        
        # 读取响应
        time.sleep(0.5)
        response = ser.read(20)
        
        if len(response) > 0:
            print(f"收到响应: {response.decode('ascii', errors='replace')}")
            ser.close()
            print("初始化命令执行成功!")
            return True
        else:
            print("未收到响应，尝试ModBus协议...")
        
        ser.close()
        
        # 如果RS-485协议失败，尝试ModBus-RTU协议
        print("尝试使用ModBus-RTU协议...")
        return init_tip_modbus(port, baudrate)
        
    except serial.SerialException as e:
        print(f"串口错误: {e}")
        return False
    except Exception as e:
        print(f"执行错误: {e}")
        return False


def init_tip_modbus(port='/dev/hand', baudrate=115200):
    """
    使用ModBus-RTU协议初始化枪头（备用方式）
    
    ModBus协议：
    - 01: 从机地址
    - 06: 功能码(写单个寄存器)
    - 01 00: 寄存器地址
    - 00 01: 写入数据
    - 49 F6: CRC校验
    """
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=3
        )
        
        print(f"串口 {port} 已打开 (ModBus模式)")
        
        # ModBus-RTU: 01 06 01 00 00 01 49 F6
        command = bytes([0x01, 0x06, 0x01, 0x00, 0x00, 0x01, 0x49, 0xF6])
        
        print(f"发送命令(ModBus): {command.hex().upper()}")
        ser.write(command)
        
        # 读取响应
        time.sleep(0.5)
        response = ser.read(8)
        
        if len(response) > 0:
            print(f"收到响应: {response.hex().upper()}")
            # 检查响应
            if response[0] == 0x01 and response[1] == 0x06:
                print("初始化命令执行成功!")
                ser.close()
                return True
        
        ser.close()
        return False
        
    except serial.SerialException as e:
        print(f"串口错误: {e}")
        return False
    except Exception as e:
        print(f"执行错误: {e}")
        return False


def main():
    """主函数"""
    # 可以通过命令行参数指定串口
    port = sys.argv[1] if len(sys.argv) > 1 else '/dev/hand'
    
    print("="*50)
    print("移液枪初始化 (YIYEQIANG_INIT)")
    print(f"串口: {port}")
    print("="*50)
    
    result = init_tip(port)
    
    if result:
        print("\n执行完成: 成功")
        sys.exit(0)
    else:
        print("\n执行完成: 失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
