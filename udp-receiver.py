import socket
import json
import time

# 监听所有网卡
UDP_IP = "0.0.0.0"

# 必须和发送端一致
UDP_PORT = 22222

# 创建UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# 绑定端口
sock.bind((UDP_IP, UDP_PORT))

print(f"UDP监听中: {UDP_IP}:{UDP_PORT}")

while True:

    try:

        # 接收数据
        data, addr = sock.recvfrom(1024)

        # bytes -> string
        msg = data.decode("utf-8")

        # string -> dict
        position = json.loads(msg)

        print("=" * 50)
        print("发送方:", addr)

        # 判断是否丢失Tag
        if position["id"] == -99:

            print("未检测到Tag")

        else:

            print(f"Tag ID : {position['id']}")
            print(f"X      : {position['x']:.3f}")
            print(f"Y      : {position['y']:.3f}")
            print(f"Angle  : {position['angle']:.3f}")
        time.sleep(0.5)
    except Exception as e:

        print("接收错误:", e)

        time.sleep(0.1)