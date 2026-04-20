import os
import time
from Robotic_Arm.rm_robot_interface import *

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

def get_sequential_filepath(base_path, prefix="trajectory", ext="txt"):
    """
    生成按编号排序的轨迹文件路径 (.txt格式)。

    参数:
        base_path (str): 保存轨迹的目录路径。
        prefix (str): 文件名前缀。
        ext (str): 文件扩展名。

    返回:
        str: 按编号排序的完整文件路径。
    """
    os.makedirs(base_path, exist_ok=True)
    existing_files = [f for f in os.listdir(base_path) if f.startswith(prefix) and f.endswith(f".{ext}")]
    numbers = [int(f.split('_')[-1].split('.')[0]) for f in existing_files if f.split('_')[-1].split('.')[0].isdigit()]
    next_number = max(numbers, default=0) + 1
    return os.path.join(base_path, f"{prefix}_{next_number:03d}.{ext}")

file_path = get_sequential_filepath("/home/maic/10-robotgui/src/vertical_grab/code/robot_arm/Path")
print("生成的轨迹文件路径:", file_path)

class RobotArmController:
    def __init__(self, ip, port, level=3, mode=2):
        """
        初始化并连接到机械臂。

        参数:
            ip (str): 机械臂的IP地址。
            port (int): 端口号。
            level (int, 可选): 连接级别。默认为3。
            mode (int, 可选): 线程模式 (0: 单线程, 1: 双线程, 2: 三线程)。默认为2。
        """
        self.thread_mode = rm_thread_mode_e(mode)
        self.robot = RoboticArm(self.thread_mode)
        self.handle = self.robot.rm_create_robot_arm(ip, port, level)

        if self.handle.id == -1:
            print("\n连接机械臂失败\n")
            exit(1)
        else:
            print(f"\n成功连接到机械臂: {self.handle.id}\n")

    def disconnect(self):
        """
        断开与机械臂的连接。

        返回:
            None
        """
        handle = self.robot.rm_delete_robot_arm()
        if handle == 0:
            print("\n成功断开与机械臂的连接\n")
        else:
            print("\n断开与机械臂的连接失败\n")

    def demo_drag_teach(self, trajectory_record):
        """
        启动拖动教学模式。

        参数:
            trajectory_record (int): 0表示不记录轨迹,1表示记录轨迹。

        返回:
            None
        """
        result = self.robot.rm_start_drag_teach(trajectory_record)
        if result == 0:
            print("拖动教学已开始")
        else:
            print("启动拖动教学失败")

        input("拖动教学已开始,完成拖动操作后按Enter键继续...")

        result = self.robot.rm_stop_drag_teach()
        if result == 0:
            print("拖动教学已停止")
        else:
            print("停止拖动教学失败")

    def demo_save_trajectory(self, file_path):
        """
        保存记录的轨迹。

        参数:
            file_path (str, 可选): 保存轨迹文件的路径。

        返回:
            int: 如果成功,返回轨迹点的总数,否则返回None。
        """
        # 确保目标目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        result = self.robot.rm_save_trajectory(file_path)
        if result[0] == 0:
            print("轨迹保存成功,轨迹点总数:", result[1])
            return result[1]
        else:
            print("保存轨迹失败")
            return None

    def add_lines_to_file(self, file_path, type_value):
        """
        向轨迹文件中添加特定行。

        参数:
            file_path (str): 轨迹文件的路径。
            type_value (int): 要添加到文件的类型值。

        返回:
            None
        """
        # 根据自由度设置file_value
        robot_info = self.robot.rm_get_robot_info()
        if robot_info[1]['arm_dof'] == 6:
            file_value = 6
        elif robot_info[1]['arm_dof'] == 7:
            file_value = 7
        else:
            raise ValueError("无效的自由度,必须是6或7")

        # 定义要添加的行,使用参数化值
        lines_to_add = [f'{{"file":{file_value}}}\n',
                        f'{{"name":"Folder","num":1,"type":{type_value},"enabled":true,"parent_number":0}}\n']

        # 读取原始文件内容
        with open(file_path, 'r+', encoding='utf-8') as file:
            original_content = file.read()

            # 将文件指针移动到开头
            file.seek(0)

            # 写入新行和原始内容
            file.writelines(lines_to_add)
            file.write(original_content)

    def demo_send_project(self, file_path, plan_speed=20, only_save=0, save_id=100, step_flag=0, auto_start=0, project_type=0):
        """
        向机械臂发送项目。

        参数:
            file_path (str): 要发送的文件路径。
            plan_speed (int, 可选): 规划速度比例。默认为20。
            only_save (int, 可选): 0表示运行文件,1表示仅保存文件而不运行。默认为0。
            save_id (int, 可选): 在控制器中保存的ID。默认为1。
            step_flag (int, 可选): 设置步进模式,1表示设置步进模式,0表示设置正常模式。默认为0。
            auto_start (int, 可选): 设置默认在线编程文件,1表示设置为默认,0表示设置为非默认。默认为0。
            project_type (int, 可选): 设置项目文件类型,1表示设置为拖动轨迹,0表示设置为在线编程文件。默认为0。

        返回:
            None
        """
        # 检查文件路径是否存在
        if not os.path.exists(file_path):
            print("文件路径不存在:", file_path)
            return

        send_project = rm_send_project_t(file_path, plan_speed, only_save, save_id, step_flag, auto_start, project_type)
        result = self.robot.rm_send_project(send_project)

        if result[0] == 0:
            if result[1] == -1:
                print("项目发送并运行成功")
            elif result[1] == 0:
                print("项目发送成功但未运行,数据长度验证失败")
            else:
                print("项目发送成功但运行失败,问题项目行数:", result[1])
        else:
            print("发送项目失败,错误代码:", result[0])

    def demo_get_program_run_state(self, time_sleep, max_retries=10):
        """
        获取程序的运行状态。

        参数:
            time_sleep (int): 重试之间的睡眠时间。
            max_retries (int, 可选): 最大重试次数。默认为10。

        返回:
            None
        """
        retries = 0
        while retries < max_retries:
            time.sleep(time_sleep)
            result = self.robot.rm_get_program_run_state()

            if result[0] == 0:
                print("程序运行状态:", result[1])
                if result[1] == 0:
                    print("程序已结束")
                    break
            else:
                print("查询失败,错误代码:", result[0])

            retries += 1

        if retries == max_retries:
            print("达到最大查询次数,退出")

    def demo_set_arm_pause(self):
        """
        暂停机械臂。

        参数:
            None

        返回:
            None
        """
        result = self.robot.rm_set_arm_pause()
        if result == 0:
            print("机械臂暂停成功")
        else:
            print("暂停机械臂失败")

    def demo_set_arm_continue(self):
        """
        继续机械臂。

        参数:
            None

        返回:
            None
        """
        result = self.robot.rm_set_arm_continue()
        if result == 0:
            print("机械臂继续成功")
        else:
            print("继续机械臂失败")

    def demo_set_arm_stop(self):
        """
        立即停止机械臂。

        参数:
            None

        返回:
            None
        """
        result = self.robot.rm_set_arm_stop()
        if result == 0:
            print("机械臂立即停止")
        else:
            print("立即停止机械臂失败")

    def demo_set_arm_slow_stop(self):
        """
        慢慢停止机械臂的轨迹。

        参数:
            None

        返回:
            None
        """
        result = self.robot.rm_set_arm_slow_stop()
        if result == 0:
            print("机械臂轨迹慢慢停止成功")
        else:
            print("慢慢停止机械臂轨迹失败")


if __name__ == "__main__":
    # 创建机械臂控制器实例并连接到机械臂
    robot_controller = RobotArmController("192.168.3.18", 8080)

    # 获取API版本
    print("\nAPI 版本:", rm_api_version(), "\n")

    # 拖动教学
    robot_controller.demo_drag_teach(1)

    # 保存轨迹
    lines = robot_controller.demo_save_trajectory(file_path)

    # 断开机械臂连接
    robot_controller.disconnect()
