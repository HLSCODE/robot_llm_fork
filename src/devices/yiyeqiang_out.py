"""
移液枪弹出枪头 (YIYEQIANG_OUT)
使用ModBus协议向寄存器地址 0x0107 写入数据 0x0001
发送十六进制：01 06 01 07 00 01 F8 37

使用方法：
    python YIYEQIANG_OUT.py
"""
import serial
import time
import sys


def eject_tip(port='COM4', baudrate=115200):
    """
    弹出枪头 - 使用ModBus协议
    
    Args:
        port: 串口号，默认COM4
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
        
        # ModBus协议：向寄存器地址 0x0107 写入数据 0x0001
        # 发送十六进制：01 06 01 07 00 01 F8 37
        # 01: 从机地址
        # 06: 功能码(写单个寄存器)
        # 01 07: 寄存器地址
        # 00 01: 写入数据
        # F8 37: CRC校验
        
        command = bytes([0x01, 0x06, 0x01, 0x07, 0x00, 0x01, 0xF8, 0x37])
        
        print(f"发送命令: {command.hex().upper()}")
        ser.write(command)
        
        # 读取响应
        time.sleep(0.5)
        response = ser.read(8)
        
        if len(response) > 0:
            print(f"收到响应: {response.hex().upper()}")
            # 检查响应是否正确
            if response[0] == 0x01 and response[1] == 0x06:
                print("弹出枪头命令执行成功!")
                ser.close()
                return True
        else:
            print("未收到响应")
        
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
    print("移液枪弹出枪头 (YIYEQIANG_OUT)")
    print(f"串口: {port}")
    print("="*50)
    
    result = eject_tip(port)
    
    if result:
        print("\n执行完成: 成功")
        sys.exit(0)
    else:
        print("\n执行完成: 失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
