# -*- coding: utf-8 -*-
from .tcp_client import TCPClient
from ..core.config_loader import Config


class RobotMoveController:
    def __init__(self, server_host=None, server_port=None, client_bind_port=None):
        """
        初始化移动机器人移动控制器。
        :param server_host: 服务器 IP 地址（默认从 config.env 读取）
        :param server_port: 服务器端口（默认从 config.env 读取）
        :param client_bind_port: 客户端绑定端口（默认从 config.env 读取）
        """
        config = Config.get_instance()
        move_config = config.get_move_controller_config()
        
        self.server_host = server_host if server_host is not None else move_config["host"]
        self.server_port = server_port if server_port is not None else move_config["port"]
        self.client_bind_port = client_bind_port if client_bind_port is not None else move_config["client_bind_port"]
        self.client = None
        self.last_result = None  # 添加last_result属性

    def connect(self):
        """连接到服务器"""
        try:
            if self.client is None:  # 如果未连接，则创建新的客户端实例
                self.client = TCPClient(
                    host=self.server_host,
                    port=self.server_port,
                    bind_port=self.client_bind_port
                )
                self.client.connect()
                print(f"Connected to server at {self.server_host}:{self.server_port}")
        except Exception as e:
            print(f"Connection failed: {e}")
            raise

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

    def move_slowly(self, valueY):
        """
        缓慢移动底盘。
        :param valueY: 移动的目标值
        :return: True 如果移动成功，False 如果失败
        """
        try:
            if self.client is None:
                self.connect()  # 如果未连接，则先连接

            # 发送命令
            command = {"cmd": 2, "id": valueY, "cid": 0}
            self.send_command(command)

            # 监听响应并解析
            response = self.listen_for_responses()
            if isinstance(response, dict) and response.get("cmd") == 2 and "result" in response:
                if response["result"]:
                    print(f"Slow move to position ({valueY}, 0) succeeded.")
                    return True
                else:
                    print(f"Slow move to position ({valueY}, 0) failed.")
                    return False
            else:
                print("Invalid response format.")
                return False
        except Exception as e:
            print(f"Error in move_slowly: {e}")
            return False


if __name__ == "__main__":
    # 创建移动机器人控制器实例
    controller = RobotMoveController()

    # 测试移动到底盘指定位置
    success = controller.move_to_position(0, 0)
    print(f"Move to position result: {success}")

    # 测试缓慢移动底盘
    success = controller.move_slowly(10)
    print(f"Slow move result: {success}")

    # 关闭连接（在所有操作完成后手动关闭）
    controller.close()

