from __future__ import annotations

import asyncio
from functools import partial
import logging

from ...application import DataCollectionError, DataCollectionState
from ...application import websocket_teleoperation_owner
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
            await asyncio.to_thread(
                self._server._services.teleoperation.start,
                self._owner_id(websocket),
                arms_to_start,
            )
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
            owner_id = self._owner_id(websocket)
            owner = self._server._services.teleoperation.snapshot().owner(
                owner_id
            )
            if owner is None or not owner.controls(arm):
                await websocket.send(
                    self._server._json_msg(
                        {"event": "teleop_error", "message": f"{arm}臂未启动遥操作模式"}
                    )
                )
                return

            # 立即发送到机械臂
            if self._server._robot_system:
                try:
                    command = await asyncio.to_thread(
                        partial(
                            self._server._services.teleoperation.follow,
                            owner_id,
                            arm,
                            joints,
                            follow=follow,
                            trajectory_mode=trajectory_mode,
                        )
                    )
                    if command.command_count % 10 == 0:
                        logger.debug(
                            "遥操作指令 #%d: arm=%s, joints=%s",
                            command.command_count,
                            arm,
                            joints,
                        )
                except Exception as e:
                    logger.error(
                        "遥操作执行异常: arm=%s error=%s",
                        arm,
                        str(e),
                    )
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "teleop_error", "message": f"执行异常: {str(e)}"}
                        )
                    )

            if grip is not None:
                self._server._schedule_background_task(
                    self._execute_grip_async(owner_id, arm, grip),
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
                owner_id = self._owner_id(websocket)
                owner = self._server._services.teleoperation.snapshot().owner(
                    owner_id
                )
                if owner is None or not owner.controls(arm_name):
                    await websocket.send(
                        self._server._json_msg(
                            {
                                "event": "teleop_error",
                                "message": f"{arm_name}臂未启动遥操作模式",
                            }
                        )
                    )
                    return

            # 并行执行双臂指令
            if self._server._robot_system:
                try:
                    arms = tuple(joints_data)
                    results = await asyncio.gather(
                        *(
                            asyncio.to_thread(
                                partial(
                                    self._server._services.teleoperation.follow,
                                    owner_id,
                                    arm_name,
                                    joints_data[arm_name],
                                    follow=follow,
                                    trajectory_mode=trajectory_mode,
                                )
                            )
                            for arm_name in arms
                        )
                    )
                    success_results = dict(zip(arms, results, strict=True))
                    if any(
                        result.command_count % 10 == 0
                        for result in success_results.values()
                    ):
                        logger.debug(
                            "双臂遥操作指令: counts=%s",
                            {
                                arm_name: result.command_count
                                for arm_name, result in success_results.items()
                            },
                        )
                except Exception as e:
                    logger.error(
                        "双臂遥操作执行异常: %s", str(e)
                    )
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "teleop_error", "message": f"执行异常: {str(e)}"}
                        )
                    )

            # 处理双臂夹爪指令
            if isinstance(grip, dict):
                for arm_name, grip_val in grip.items():
                    if grip_val is not None:
                        self._server._schedule_background_task(
                            self._execute_grip_async(
                                owner_id,
                                arm_name,
                                grip_val,
                            ),
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
        if (
            self._server._services.data_collection.snapshot().state
            is not DataCollectionState.IDLE
        ):
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "demo_record_error",
                        "message": (
                            "数据采集会话正在共享遥操作控制，"
                            "请先结束数据采集会话"
                        ),
                    }
                )
            )
            return

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

        stopped = await asyncio.to_thread(
            self._server._services.teleoperation.stop,
            self._owner_id(websocket),
            arms_to_stop,
        )
        total_counts = {
            arm_name: (
                stopped.command_count(arm_name)
                if stopped is not None
                else 0
            )
            for arm_name in arms_to_stop
        }

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
        """Start a data-collection session through the application service."""
        task = data.get("task")
        description = data.get("description", "")

        if not isinstance(task, str) or not task.strip():
            await websocket.send(
                self._server._json_msg(
                    {"event": "demo_record_error", "message": "缺少task参数"}
                )
            )
            return

        try:
            result = await asyncio.to_thread(
                self._server._services.data_collection.start_session,
                task,
                description if isinstance(description, str) else "",
            )
        except (DataCollectionError, ValueError) as exc:
            await self._send_data_collection_error(websocket, exc)
            return

        logger.info(
            "数据采集会话已启动: task=%s, next_episode_id=%d",
            result.task,
            result.next_episode_id,
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "demo_session_started",
                    "task": result.task,
                    "next_episode_id": result.next_episode_id,
                    "message": result.message,
                }
            )
        )

    async def _handle_demo_record_start(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """Start one episode and join the shared teleoperation session."""
        try:
            result = await asyncio.to_thread(
                self._server._services.data_collection.start_episode
            )
        except DataCollectionError as exc:
            await self._send_data_collection_error(websocket, exc)
            return

        logger.info(
            "episode %d 开始记录（已共享遥操作控制会话）",
            result.episode_id,
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "demo_record_started",
                    "episode_id": result.episode_id,
                    "message": result.message + "（已自动启动遥操作模式）",
                }
            )
        )

    async def _handle_demo_record_stop(self, websocket, data: WebSocketRequest) -> None:
        """Stop and persist one episode without ending shared control."""
        try:
            result = await asyncio.to_thread(
                self._server._services.data_collection.stop_episode
            )
        except DataCollectionError as exc:
            await self._send_data_collection_error(websocket, exc)
            return

        logger.info(
            "episode %d 已保存，共 %d 帧",
            result.episode_id,
            result.frames,
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "demo_record_stopped",
                    "episode_id": result.episode_id,
                    "frames": result.frames,
                    "message": result.message,
                }
            )
        )

    async def _handle_demo_session_end(self, websocket, data: WebSocketRequest) -> None:
        """End the session and release recorder, camera and control resources."""
        try:
            result = await asyncio.to_thread(
                self._server._services.data_collection.end_session
            )
        except DataCollectionError as exc:
            await self._send_data_collection_error(websocket, exc)
            return

        logger.info("数据采集会话已结束（已释放共享遥操作控制）")
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "demo_session_ended",
                    "message": result.message + "（已自动停止遥操作模式）",
                }
            )
        )

    async def close_data_collection(self) -> None:
        """Release application-owned collection resources on host cleanup."""

        await asyncio.to_thread(
            self._server._services.data_collection.close
        )

    async def _send_data_collection_error(
        self,
        websocket,
        error: Exception,
    ) -> None:
        payload: dict[str, object] = {
            "event": "demo_record_error",
            "message": str(error),
        }
        if isinstance(error, DataCollectionError):
            payload["detail_code"] = error.code.value
            if error.episode_id is not None:
                payload["episode_id"] = error.episode_id
            if error.frames is not None:
                payload["frames"] = error.frames
        await websocket.send(self._server._json_msg(payload))

    async def _execute_grip_async(
        self,
        owner_id: str,
        arm: str,
        position: int,
    ) -> None:
        """在线程池中异步执行夹爪位置指令，不阻塞关节指令流"""
        if self._server._robot_system is None:
            return
        position = max(0, min(1000, int(position)))
        try:
            await asyncio.to_thread(
                self._server._services.teleoperation.set_gripper,
                owner_id,
                arm,
                position,
            )
            logger.info("遥操作夹爪位置: %s臂 %d", arm, position)
        except Exception as e:
            logger.error("遥操作夹爪执行异常: arm=%s, error=%s", arm, str(e))

    def _owner_id(self, websocket) -> str:
        return websocket_teleoperation_owner(
            self._server._client_id(websocket)
        )
