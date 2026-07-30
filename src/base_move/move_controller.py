# -*- coding: utf-8 -*-
from .tcp_client import TCPClient


class RobotMoveController:
    def __init__(
        self,
        server_host: str,
        server_port: int,
        client_bind_port: int | None,
    ):
        """Create a controller from an explicit immutable settings snapshot."""
        self.server_host = server_host
        self.server_port = server_port
        self.client_bind_port = client_bind_port
        self.client = None
        self.last_result = None  # 添加last_result属性

    def connect(self):
        """连接到服务器"""
        if self.client is None:  # 如果未连接，则创建新的客户端实例
            self.client = TCPClient(
                host=self.server_host,
                port=self.server_port,
                bind_port=self.client_bind_port
            )
            self.client.connect()
            print(f"Connected to server at {self.server_host}:{self.server_port}")

    def send_command(self, command):
        """发送命令到服务器"""
        try:
            if self.client is None:
                raise Exception("Client is not connected.")
            self.client.send_command(command)
            print(f"Command sent: {command}")
        except Exception as e:
            print(f"Failed to send command: {e}")
            raise

    def get_last_result(self):
        """获取最后一次执行的结果"""
        return self.last_result

    def listen_for_responses(self):
        """
        监听服务器响应。
        :return: 最终结果响应或 None（如果未收到有效结果）
        """
        try:
            if self.client is None:
                raise Exception("Client is not connected.")

            while True:
                response = self.client.listen_for_responses()
                print(f"Response received: {response}")

                if isinstance(response, dict):
                    # 检查是否是状态更新消息
                    if "execute" in response:
                        self.handle_status_update(response)
                    # 检查是否是最终结果消息
                    elif "result" in response:
                        self.last_result = response["result"]  # 保存结果
                        return response  # 返回最终结果消息
                else:
                    print("Invalid response format.")
                    return None
        except Exception as e:
            print(f"Error receiving response: {e}")
            raise

    def handle_status_update(self, status):
        """
        处理状态更新消息。
        :param status: 状态更新消息
        """
        try:
            cmd = status.get("cmd")
            id_value = status.get("id")
            cid = status.get("cid")
            execute_time = status.get("execute")

            print(f"Status update - Command: {cmd}, ID: {id_value}, CID: {cid}, Execute Time: {execute_time} seconds")
        except Exception as e:
            print(f"Error handling status update: {e}")

    def close(self):
        """关闭连接"""
        try:
            if self.client:
                self.client.close()
                self.client = None  # 确保客户端对象被清空
                print("Connection closed.")
        except Exception as e:
            print(f"Error closing connection: {e}")
            raise

    def move_to_position(self, id, cid):
        """
        移动到底盘指定位置。
        :param id: 目标位置 ID
        :param cid: 目标位置 CID
        :return: True 如果移动成功，False 如果失败
        """
        try:
            if self.client is None:
                self.connect()  # 如果未连接，则先连接

            # 发送命令
            command = {"cmd": 1, "id": id, "cid": cid}
            self.send_command(command)

            # 监听响应并解析
            response = self.listen_for_responses()
            if isinstance(response, dict) and response.get("cmd") == 1 and "result" in response:
                if response["result"]:
                    print(f"Move to position ({id}, {cid}) succeeded.")
                    return True
                else:
                    print(f"Move to position ({id}, {cid}) failed.")
                    return False
            else:
                print("Invalid response format.")
                return False
        except Exception as e:
            print(f"Error in move_to_position: {e}")
            return False

    def move_slowly(self, x, y, angle):
        """
        缓慢移动底盘（按相对距离和角度）。
        :param x: X 方向移动距离，单位 cm
        :param y: Y 方向移动距离，单位 cm
        :param angle: 旋转角度，单位 度
        :return: True 如果移动成功，False 如果失败
        """
        try:
            if self.client is None:
                self.connect()  # 如果未连接，则先连接

            # 发送命令
            command = {"cmd": 2, "x": x, "y": y, "angle": angle}
            self.send_command(command)

            # 监听响应并解析
            response = self.listen_for_responses()
            if isinstance(response, dict) and response.get("cmd") == 2 and "result" in response:
                if response["result"]:
                    print(f"Slow move (x={x}, y={y}, angle={angle}) succeeded.")
                    return True
                else:
                    print(f"Slow move (x={x}, y={y}, angle={angle}) failed.")
                    return False
            else:
                print("Invalid response format.")
                return False
        except Exception as e:
            print(f"Error in move_slowly: {e}")
            return False
