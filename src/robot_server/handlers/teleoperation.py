from __future__ import annotations

import asyncio
import logging

from ...device_runtime.ids import ROBOT_SYSTEM
from ..protocol import WebSocketRequest
from .base import WebSocketHandlerHost

logger = logging.getLogger(__name__)


class TeleoperationWebSocketHandler:
    def __init__(self, server: WebSocketHandlerHost) -> None:
        self._server = server

    async def _handle_teleop_init(self, websocket, data: WebSocketRequest) -> None:
        """
        遥操作初始化：移动机械臂到指定关节姿态
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_init", "arm": "左", "joints": [j1,j2,j3,j4,j5,j6]}
        - 双臂: {"action": "teleop_init", "joints": {"左": [j1,j2,j3,j4,j5,j6], "右": [j1,j2,j3,j4,j5,j6]}}
        响应: {"event": "teleop_init_completed", "arm": "左", "message": "初始化完成"}
        """
        arm = data.get("arm")
        joints_data = data.get("joints")

        # 检查机械臂是否已连接
        if self._server._robot_system is None:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "机械臂控制器未初始化"}
                )
            )
            return

        # 判断单臂还是双臂
        if arm:
            # 单臂模式：joints是列表
            if not isinstance(joints_data, list):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": "单臂模式需要joints为列表"}
                    )
                )
                return

            joints = joints_data
            if len(joints) != 6:
                await websocket.send(
                    self._server._json_msg(
                        {
                            "event": "error",
                            "message": f"关节角度数量错误：需要6个，实际{len(joints)}个",
                        }
                    )
                )
                return

            logger.info("遥操作初始化: %s臂移动到 %s", arm, joints)

            try:
                success = (
                    self._server._services.manual_control.initialize_teleoperation(
                        arm,
                        joints,
                    )
                )
                if success:
                    logger.info("遥操作初始化完成: %s臂", arm)
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_init_completed",
                                "arm": arm,
                                "message": "初始化完成",
                            }
                        )
                    )
                else:
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "error", "message": "初始化移动失败"}
                        )
                    )
            except Exception as e:
                logger.error("遥操作初始化异常: %s", str(e))
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"初始化异常: {str(e)}"}
                    )
                )

        else:
            # 双臂模式：joints是字典
            if not isinstance(joints_data, dict):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": "双臂模式需要joints为字典"}
                    )
                )
                return

            # 验证每个臂的关节角度
            for arm_name, joints in joints_data.items():
                if arm_name not in ["左", "右"]:
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "error", "message": f"未知的臂名称: {arm_name}"}
                        )
                    )
                    return

                if len(joints) != 6:
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "error",
                                "message": f"{arm_name}臂关节角度数量错误：需要6个，实际{len(joints)}个",
                            }
                        )
                    )
                    return

            logger.info(
                "双臂遥操作初始化: 左=%s, 右=%s",
                joints_data.get("左"),
                joints_data.get("右"),
            )

            # 并行执行双臂初始化
            success_results = {}
            try:
                for arm_name, joints in joints_data.items():
                    success = (
                        self._server._services.manual_control.initialize_teleoperation(
                            arm_name,
                            joints,
                        )
                    )
                    success_results[arm_name] = success

                if all(success_results.values()):
                    logger.info("双臂遥操作初始化完成")
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_init_completed_dual",
                                "message": "双臂初始化完成",
                            }
                        )
                    )
                else:
                    failed_arms = [
                        arm for arm, success in success_results.items() if not success
                    ]
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "error",
                                "message": f"部分臂初始化失败: {failed_arms}",
                            }
                        )
                    )
            except Exception as e:
                logger.error("双臂遥操作初始化异常: %s", str(e))
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"初始化异常: {str(e)}"}
                    )
                )

    async def _handle_teleop_start(self, websocket, data: WebSocketRequest) -> None:
        """
        启动遥操作模式
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_start", "arm": "左"}
        - 多臂: {"action": "teleop_start", "arms": ["左", "右"]}
        - 双臂: {"action": "teleop_start"} (无参数，默认启动所有臂)
        响应: {"event": "teleop_started", "arms": ["左"], "message": "遥操作模式已启动"}
        """
        arm = data.get("arm")
        arms_list = data.get("arms")

        # 检查是否正在执行其他任务
        if self._server._services.execution.snapshot().active:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "有任务正在执行，无法启动遥操作"}
                )
            )
            return

        # 检查机械臂是否已连接
        if self._server._robot_system is None:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "机械臂控制器未初始化"}
                )
            )
            return

        # 确定要启动的臂
        if arm:
            # 单臂模式
            arms_to_start = [arm]
        elif arms_list:
            # 多臂模式
            arms_to_start = arms_list
        else:
            # 默认启动所有臂（双臂）
            arms_to_start = ["左", "右"]

        # 验证臂名称
        for arm_name in arms_to_start:
            if arm_name not in ["左", "右"]:
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"未知的臂名称: {arm_name}"}
                    )
                )
                return

        try:
            self._server._services.teleoperation.start()
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": f"遥操作资源申请失败: {exc}",
                    }
                )
            )
            return

        # 启动指定臂的遥操作模式
        for arm_name in arms_to_start:
            self._server._teleop_modes[arm_name] = True
            self._server._teleop_msg_counts[arm_name] = 0

        logger.info("遥操作模式已启动: %s", arms_to_start)
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "teleop_started",
                    "arms": arms_to_start,
                    "message": "遥操作模式已启动",
                }
            )
        )

    async def _handle_teleop_joint(self, websocket, data: WebSocketRequest) -> None:
        """
        处理遥操作关节指令（50Hz）
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_joint", "arm": "左", "joints": [j1,j2,j3,j4,j5,j6], "follow": true}
        - 双臂: {"action": "teleop_joint", "joints": {"左": [j1,j2,j3,j4,j5,j6], "右": [j1,j2,j3,j4,j5,j6]}, "follow": false}
        响应: 仅在执行失败时返回 {"event": "teleop_error", "message": "..."}
        """
        arm = data.get("arm")
        joints_data = data.get("joints")
        follow = data.get("follow", False)  # 默认False（平滑模式）
        trajectory_mode = data.get("trajectory_mode", 0)
        grip = data.get(
            "grip"
        )  # 夹爪位置原始值（0=闭合，1000=完全张开），仅在值变化时执行

        # 判断单臂还是双臂
        if arm:
            # 单臂模式
            if not isinstance(joints_data, list):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "teleop_error", "message": "单臂模式需要joints为列表"}
                    )
                )
                return

            joints = joints_data
            if len(joints) != 6:
                await websocket.send(
                    self._server._json_msg(
                        {
                            "event": "teleop_error",
                            "message": f"关节角度数量错误：需要6个，实际{len(joints)}个",
                        }
                    )
                )
                return

            # 检查该臂是否已启动遥操作
            if not self._server._teleop_modes.get(arm):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "teleop_error", "message": f"{arm}臂未启动遥操作模式"}
                    )
                )
                return

            # 采样日志：每10条记录一次
            self._server._teleop_msg_counts[arm] += 1
            if self._server._teleop_msg_counts[arm] % 10 == 0:
                logger.debug(
                    "遥操作指令 #%d: arm=%s, joints=%s",
                    self._server._teleop_msg_counts[arm],
                    arm,
                    joints,
                )

            # 立即发送到机械臂
            if self._server._robot_system:
                try:
                    success = self._server._services.teleoperation.follow(
                        arm,
                        joints,
                        follow=follow,
                        trajectory_mode=trajectory_mode,
                    )
                    if not success:
                        logger.warning(
                            "遥操作指令 #%d 执行失败",
                            self._server._teleop_msg_counts[arm],
                        )
                        await websocket.send(
                            self._server._json_msg(
                                {"event": "teleop_error", "message": "关节指令执行失败"}
                            )
                        )
                except Exception as e:
                    logger.error(
                        "遥操作执行异常 #%d: %s",
                        self._server._teleop_msg_counts[arm],
                        str(e),
                    )
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "teleop_error", "message": f"执行异常: {str(e)}"}
                        )
                    )

            # 处理夹爪指令（直接传原始位置值，仅在值变化时触发）
            if grip is not None and grip != self._server._last_grip.get(arm):
                self._server._last_grip[arm] = grip
                self._server._schedule_background_task(
                    self._execute_grip_async(arm, grip),
                    name=f"WebSocketGrip-{arm}",
                )

        else:
            # 双臂模式
            if not isinstance(joints_data, dict):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "teleop_error", "message": "双臂模式需要joints为字典"}
                    )
                )
                return

            # 验证每个臂的关节角度
            for arm_name, joints in joints_data.items():
                if arm_name not in ["左", "右"]:
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_error",
                                "message": f"未知的臂名称: {arm_name}",
                            }
                        )
                    )
                    return

                if len(joints) != 6:
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_error",
                                "message": f"{arm_name}臂关节角度数量错误：需要6个，实际{len(joints)}个",
                            }
                        )
                    )
                    return

                # 检查该臂是否已启动遥操作
                if not self._server._teleop_modes.get(arm_name):
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_error",
                                "message": f"{arm_name}臂未启动遥操作模式",
                            }
                        )
                    )
                    return

            # 采样日志：每10条记录一次（使用左臂计数）
            self._server._teleop_msg_counts["左"] += 1
            self._server._teleop_msg_counts["右"] += 1
            if self._server._teleop_msg_counts["左"] % 10 == 0:
                logger.debug(
                    "双臂遥操作指令 #%d: 左=%s, 右=%s",
                    self._server._teleop_msg_counts["左"],
                    joints_data.get("左"),
                    joints_data.get("右"),
                )

            # 并行执行双臂指令
            if self._server._robot_system:
                try:
                    success_results = {}
                    for arm_name, joints in joints_data.items():
                        success = self._server._services.teleoperation.follow(
                            arm_name,
                            joints,
                            follow=follow,
                            trajectory_mode=trajectory_mode,
                        )
                        success_results[arm_name] = success

                    if not all(success_results.values()):
                        failed_arms = [
                            arm
                            for arm, success in success_results.items()
                            if not success
                        ]
                        logger.warning(
                            "双臂遥操作指令 #%d 部分执行失败: %s",
                            self._server._teleop_msg_counts["左"],
                            failed_arms,
                        )
                        await websocket.send(
                            self._server._json_msg(
                                {
                                    "event": "teleop_error",
                                    "message": f"部分臂执行失败: {failed_arms}",
                                }
                            )
                        )
                except Exception as e:
                    logger.error(
                        "双臂遥操作执行异常 #%d: %s",
                        self._server._teleop_msg_counts["左"],
                        str(e),
                    )
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "teleop_error", "message": f"执行异常: {str(e)}"}
                        )
                    )

            # 处理双臂夹爪指令
            if isinstance(grip, dict):
                for arm_name, grip_val in grip.items():
                    if (
                        arm_name in self._server._last_grip
                        and grip_val is not None
                        and grip_val != self._server._last_grip.get(arm_name)
                    ):
                        self._server._last_grip[arm_name] = grip_val
                        self._server._schedule_background_task(
                            self._execute_grip_async(arm_name, grip_val),
                            name=f"WebSocketGrip-{arm_name}",
                        )

    async def _handle_teleop_stop(self, websocket, data: WebSocketRequest) -> None:
        """
        停止遥操作模式
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_stop", "arm": "左"}
        - 多臂: {"action": "teleop_stop", "arms": ["左", "右"]}
        - 双臂: {"action": "teleop_stop"} (无参数，停止所有臂)
        响应: {"event": "teleop_stopped", "arms": ["左"], "total_counts": {"左": 100}, "message": "遥操作模式已停止"}
        """
        arm = data.get("arm")
        arms_list = data.get("arms")

        # 确定要停止的臂
        if arm:
            # 单臂模式
            arms_to_stop = [arm]
        elif arms_list:
            # 多臂模式
            arms_to_stop = arms_list
        else:
            # 默认停止所有臂（双臂）
            arms_to_stop = ["左", "右"]

        # 验证臂名称
        for arm_name in arms_to_stop:
            if arm_name not in ["左", "右"]:
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"未知的臂名称: {arm_name}"}
                    )
                )
                return

        # 记录停止前的总计数
        total_counts = {}
        for arm_name in arms_to_stop:
            total_counts[arm_name] = self._server._teleop_msg_counts[arm_name]

        # 停止指定臂的遥操作模式
        for arm_name in arms_to_stop:
            self._server._teleop_modes[arm_name] = False
            self._server._teleop_msg_counts[arm_name] = 0
            self._server._last_grip[arm_name] = None  # 重置夹爪跟踪状态
        if not any(self._server._teleop_modes.values()):
            self._server._services.teleoperation.stop()

        logger.info("遥操作模式已停止: %s，共执行指令 %s", arms_to_stop, total_counts)
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "teleop_stopped",
                    "arms": arms_to_stop,
                    "total_counts": total_counts,
                    "message": "遥操作模式已停止",
                }
            )
        )

    async def _handle_demo_session_start(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """
        开始数据采集会话
        请求: {"action": "demo_session_start", "task": "pick_bottle", "description": "抓取瓶子"}
        响应: {"event": "demo_session_started", "task": "pick_bottle", "next_episode_id": 0}
        """
        task = data.get("task")
        description = data.get("description", "")

        if not task:
            await websocket.send(
                self._server._json_msg(
                    {"event": "demo_record_error", "message": "缺少task参数"}
                )
            )
            return

        if (
            self._server._demo_recorder is not None
            or self._server._demo_camera_session is not None
        ):
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": "数据采集会话已经启动",
                    }
                )
            )
            return

        # 初始化数据采集器（延迟初始化）
        camera_session = None
        try:
            from ...data_collection import RLBenchRecorder
            from ...data_collection.config import DataCollectionConfig

            config = DataCollectionConfig()
            camera_session = self._server._services.camera_access.open_depth(
                "websocket-data-collection"
            )
            self._server._services.devices.initialize(ROBOT_SYSTEM)
            recorder = RLBenchRecorder(
                robot_state_reader=(self._server._services.robot_query.state_reader()),
                camera_source=camera_session.camera,
                config=config,
            )
            result = recorder.start_session(task, description)
        except Exception as exc:
            if camera_session is not None:
                camera_session.close()
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": f"数据采集会话启动失败: {exc}",
                    }
                )
            )
            return

        if result.get("success"):
            self._server._demo_recorder = recorder
            self._server._demo_camera_session = camera_session
            # 更新会话状态
            self._server._demo_session["active"] = True
            self._server._demo_session["task"] = task
            self._server._demo_session["description"] = description
            self._server._demo_session["next_episode_id"] = result["next_episode_id"]

            logger.info(
                f"数据采集会话已启动: task={task}, next_episode_id={result['next_episode_id']}"
            )

            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_session_started",
                        "task": task,
                        "next_episode_id": result["next_episode_id"],
                        "message": result["message"],
                    }
                )
            )
        else:
            camera_session.close()
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": result.get("message", "会话启动失败"),
                    }
                )
            )

    async def _handle_demo_record_start(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """
        开始记录单条episode（自动启动遥操作模式）
        请求: {"action": "demo_record_start"}
        响应: {"event": "demo_record_started", "episode_id": 0}
        """
        if not self._server._demo_session["active"]:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": "会话未启动，请先发送demo_session_start",
                    }
                )
            )
            return
        recorder = self._server._demo_recorder
        if recorder is None:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": "数据采集器不可用",
                    }
                )
            )
            return

        teleoperation_was_active = self._server._services.teleoperation.active
        try:
            self._server._services.teleoperation.start()
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": f"遥操作资源申请失败: {exc}",
                    }
                )
            )
            return

        try:
            result = recorder.start_recording()
        except Exception as exc:
            if not teleoperation_was_active:
                self._server._services.teleoperation.stop()
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": f"启动数据记录失败: {exc}",
                    }
                )
            )
            return

        if result.get("success"):
            episode_id = result["episode_id"]

            # 启动双臂遥操作模式
            for arm_name in ["左", "右"]:
                self._server._teleop_modes[arm_name] = True
                self._server._teleop_msg_counts[arm_name] = 0
                self._server._last_grip[arm_name] = None  # 重置夹爪跟踪状态

            logger.info("数据采集已自动启动遥操作模式: 双臂")

            logger.info(f"episode {episode_id} 开始记录（已自动启动遥操作）")

            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_started",
                        "episode_id": episode_id,
                        "message": result["message"] + "（已自动启动遥操作模式）",
                    }
                )
            )
        else:
            if not teleoperation_was_active:
                self._server._services.teleoperation.stop()
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": result.get("message", "记录启动失败"),
                    }
                )
            )

    async def _handle_demo_record_stop(self, websocket, data: WebSocketRequest) -> None:
        """
        结束记录单条episode并保存
        请求: {"action": "demo_record_stop"}
        响应: {"event": "demo_record_stopped", "episode_id": 0, "frames": 1500}
        """
        if not self._server._demo_session["active"]:
            await websocket.send(
                self._server._json_msg(
                    {"event": "demo_record_error", "message": "会话未启动"}
                )
            )
            return

        recorder = self._server._demo_recorder
        if recorder is None:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": "数据采集器不可用",
                    }
                )
            )
            return

        # 结束记录并保存
        result = recorder.stop_recording()

        if result.get("success"):
            episode_id = result["episode_id"]
            frames = result["frames"]

            logger.info(f"episode {episode_id} 已保存，共{frames}帧")

            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_stopped",
                        "episode_id": episode_id,
                        "frames": frames,
                        "message": result["message"],
                    }
                )
            )
        else:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "episode_id": result.get("episode_id"),
                        "frames": result.get("frames", 0),
                        "message": result.get("message", "保存失败"),
                    }
                )
            )

    async def _handle_demo_session_end(self, websocket, data: WebSocketRequest) -> None:
        """
        结束数据采集会话（自动停止遥操作模式）
        请求: {"action": "demo_session_end"}
        响应: {"event": "demo_session_ended", "message": "会话已结束"}
        """
        if not self._server._demo_session["active"]:
            await websocket.send(
                self._server._json_msg(
                    {"event": "demo_record_error", "message": "会话未启动"}
                )
            )
            return

        recorder = self._server._demo_recorder
        camera_session = self._server._demo_camera_session
        if recorder is None or camera_session is None:
            try:
                await self._close_demo_recorder()
            except Exception as exc:
                logger.warning(
                    "清理不完整的数据采集会话失败: %s",
                    exc,
                )
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": "数据采集会话状态不完整，已执行清理",
                    }
                )
            )
            return

        try:
            recorder.stop_recording()
            result = recorder.end_session()
        except Exception as exc:
            result = {
                "success": False,
                "message": f"结束数据采集会话失败: {exc}",
            }
        finally:
            try:
                self._server._services.teleoperation.stop()
            finally:
                camera_session.close()
                self._server._demo_recorder = None
                self._server._demo_camera_session = None
                for arm_name in ["左", "右"]:
                    self._server._teleop_modes[arm_name] = False
                    self._server._teleop_msg_counts[arm_name] = 0
                    self._server._last_grip[arm_name] = None

                logger.info("数据采集会话已自动停止遥操作模式: 双臂")
                self._server._demo_session["active"] = False
                self._server._demo_session["task"] = None
                self._server._demo_session["description"] = None

        logger.info("数据采集会话已结束（已自动停止遥操作）")

        event = "demo_session_ended" if result.get("success") else "demo_record_error"
        await websocket.send(
            self._server._json_msg(
                {
                    "event": event,
                    "message": result["message"] + "（已自动停止遥操作模式）",
                }
            )
        )

    async def _close_demo_recorder(self) -> None:
        recorder = self._server._demo_recorder
        self._server._demo_recorder = None
        camera_session = self._server._demo_camera_session
        self._server._demo_camera_session = None
        if recorder is None and camera_session is None:
            return
        errors: list[Exception] = []
        if recorder is not None:
            try:
                await asyncio.to_thread(recorder.stop_recording)
            except Exception as exc:
                errors.append(exc)
            try:
                recorder.end_session()
            except Exception as exc:
                errors.append(exc)
        try:
            self._server._services.teleoperation.stop()
        except Exception as exc:
            errors.append(exc)
        finally:
            if camera_session is not None:
                camera_session.close()
            self._server._demo_session = {
                "active": False,
                "task": None,
                "description": None,
                "next_episode_id": 0,
            }
        if errors:
            detail = "; ".join(str(error) for error in errors)
            raise RuntimeError(f"failed to close data collection recorder: {detail}")

    async def _execute_grip_async(self, arm: str, position: int) -> None:
        """在线程池中异步执行夹爪位置指令，不阻塞关节指令流"""
        if self._server._robot_system is None:
            return
        position = max(0, min(1000, int(position)))
        try:
            await asyncio.to_thread(
                self._server._services.teleoperation.set_gripper,
                arm,
                position,
            )
            logger.info("遥操作夹爪位置: %s臂 %d", arm, position)
        except Exception as e:
            logger.error("遥操作夹爪执行异常: arm=%s, error=%s", arm, str(e))
