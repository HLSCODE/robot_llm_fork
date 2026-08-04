from __future__ import annotations

import asyncio
import base64
import logging
import threading

from ...devices.runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from ...execution import ExecutionState
from ..protocol import WebSocketRequest
from .base import WebSocketHandlerHost

logger = logging.getLogger(__name__)


class DeviceWebSocketHandler:
    def __init__(self, server: WebSocketHandlerHost) -> None:
        self._server = server

    async def _handle_status(self, websocket, data: WebSocketRequest) -> None:
        """查询设备和执行状态"""
        execution = self._server._services.execution.snapshot()
        devices = self._server._services.devices.status()
        data_collection = (
            self._server._services.data_collection.snapshot()
        )
        camera = self._server._camera_manager
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "status",
                    "devices": devices,
                    "executor": {
                        "run_id": execution.run_id,
                        "state": execution.state.value,
                        "running": execution.active,
                        "paused": execution.state is ExecutionState.PAUSED,
                        "error": execution.error,
                        "error_code": execution.error_code,
                        "error_operation": execution.error_operation,
                        "error_device_id": execution.error_device_id,
                        "error_category": execution.error_category,
                        "raw_error_code": execution.raw_error_code,
                    },
                    "sequence_length": len(
                        self._server._services.composition.sequence_entries()
                    ),
                    "data_collection": {
                        "state": data_collection.state.value,
                        "task": data_collection.task,
                        "next_episode_id": (
                            data_collection.next_episode_id
                        ),
                        "episode_id": data_collection.episode_id,
                        "recording": data_collection.recording,
                        "teleoperation_shared": (
                            data_collection.teleoperation_shared
                        ),
                    },
                    "ai_processing": self._server._ai_processing,
                    "camera": {
                        "available": camera is not None and camera.camera_count > 0,
                        "camera_count": camera.camera_count if camera else 0,
                        "cameras": camera.get_cameras_info() if camera else [],
                    },
                    "minicpm": {
                        "configured": self._server._minicpm_cfg is not None,
                        "gateway": (
                            f"{self._server._minicpm_cfg.ws_scheme}://"
                            f"{self._server._minicpm_cfg.gateway_host}"
                            f"{self._server._minicpm_cfg._port_suffix}"
                            f"{self._server._minicpm_cfg.gateway_path_prefix}"
                        )
                        if self._server._minicpm_cfg
                        else None,
                    },
                }
            )
        )

    async def _handle_init_robots(self, websocket, data: WebSocketRequest) -> None:
        """
        初始化机械臂
        请求: {"action": "init_robots"}
        """
        await websocket.send(
            self._server._json_msg(
                {"event": "log", "level": "info", "message": "开始初始化机械臂..."}
            )
        )
        try:
            await asyncio.to_thread(
                self._server._services.devices.initialize,
                ROBOT_SYSTEM,
            )
            await self._server._broadcast(
                {
                    "event": "device_status_changed",
                    "devices": self._server._services.devices.status(),
                }
            )
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"机械臂初始化异常: {exc}"}
                )
            )

    async def _handle_init_body(self, websocket, data: WebSocketRequest) -> None:
        """
        初始化身体（升降平台）
        请求: {"action": "init_body"}
        """
        try:
            await asyncio.to_thread(
                self._server._services.devices.initialize,
                BODY_AXIS,
            )

            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "log",
                        "level": "info",
                        "message": "身体控制器初始化成功",
                    }
                )
            )
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "device_status_changed",
                        "devices": self._server._services.devices.status(),
                    }
                )
            )
        except ImportError as e:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"身体模块导入失败: {e}"}
                )
            )
        except Exception as e:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"身体初始化异常: {e}"}
                )
            )

    async def _handle_disconnect(self, websocket, data: WebSocketRequest) -> None:
        """断开所有硬件连接"""
        results = await asyncio.to_thread(
            self._server._services.devices.shutdown_all,
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "disconnected",
                    "results": results,
                    "devices": self._server._services.devices.status(),
                }
            )
        )

    async def _handle_test_camera(self, websocket, data: WebSocketRequest) -> None:
        """
        通过 DeviceRuntime 测试相机（与视觉抓取使用同一实例）。
        请求: {"action": "test_camera"}
        """

        def _do_test():
            session = None
            try:
                import time

                camera_name = (
                    self._server._services.settings.vision.vision_camera_name
                    or None
                )

                session = self._server._services.camera_access.open("websocket-test")
                mgr = session.camera

                # 等待至少一路相机上线
                deadline = time.time() + 10
                online = []
                while time.time() < deadline:
                    info = mgr.get_cameras_info()
                    online = [c for c in info if c.get("online")]
                    if online:
                        break
                    time.sleep(0.3)
                else:
                    all_info = mgr.get_cameras_info()
                    errors = []
                    for c in all_info:
                        if not c.get("online"):
                            errors.append(
                                f"{c.get('name', '?')}: {c.get('error', '未知')}"
                            )
                    if errors:
                        self._server._broadcast_threadsafe(
                            {
                                "event": "camera_test_result",
                                "success": False,
                                "message": f"相机启动失败: {'; '.join(errors)}",
                            }
                        )
                    else:
                        self._server._broadcast_threadsafe(
                            {
                                "event": "camera_test_result",
                                "success": False,
                                "message": "未检测到在线相机",
                            }
                        )
                    return

                # 尝试取帧
                deadline = time.time() + 10
                while time.time() < deadline:
                    if hasattr(mgr, "get_latest_raw_frames"):
                        raw = mgr.get_latest_raw_frames(camera_name)
                        if raw is not None:
                            color, depth, intr = raw
                            if color is not None and depth is not None:
                                h, w = color.shape[:2]
                                center_dist = float(depth[h // 2, w // 2])
                                actual_name = camera_name or online[0]["name"]
                                sn = ""
                                for c in online:
                                    if c["name"] == actual_name:
                                        sn = f" SN={c['serial']}"
                                        break
                                msg = (
                                    f"相机测试成功: color={w}x{h}  "
                                    f"depth(center)={center_dist / 1000:.3f}m  "
                                    f"(camera={actual_name}{sn})"
                                )
                                self._server._broadcast_threadsafe(
                                    {
                                        "event": "camera_test_result",
                                        "success": True,
                                        "message": msg,
                                    }
                                )
                                return
                    else:
                        jpegs = mgr.get_latest_jpegs()
                        if jpegs:
                            if camera_name:
                                matched = [
                                    (n, len(b)) for s, n, b in jpegs if n == camera_name
                                ]
                                if matched:
                                    self._server._broadcast_threadsafe(
                                        {
                                            "event": "camera_test_result",
                                            "success": True,
                                            "message": f"本地摄像头测试成功: camera={matched[0][0]}",
                                        }
                                    )
                                    return
                            else:
                                name = jpegs[0][1]
                                self._server._broadcast_threadsafe(
                                    {
                                        "event": "camera_test_result",
                                        "success": True,
                                        "message": f"本地摄像头测试成功: camera={name}",
                                    }
                                )
                                return
                    time.sleep(0.2)

                self._server._broadcast_threadsafe(
                    {
                        "event": "camera_test_result",
                        "success": False,
                        "message": "取帧超时（10 秒内未获得有效帧）",
                    }
                )

            except Exception as e:
                self._server._broadcast_threadsafe(
                    {
                        "event": "camera_test_result",
                        "success": False,
                        "message": f"测试异常: {str(e)}",
                    }
                )
            finally:
                if session is not None:
                    session.close()

        threading.Thread(target=_do_test, daemon=True, name="TestCamera").start()
        await websocket.send(
            self._server._json_msg(
                {"event": "log", "level": "info", "message": "正在测试相机..."}
            )
        )

    async def _handle_camera_status(self, websocket, data: WebSocketRequest) -> None:
        """
        查询相机管理器状态
        请求: {"action": "camera_status"}
        响应: {
            "event": "camera_status",
            "available": bool,          // 是否有在线相机
            "camera_count": int,        // 在线相机数量
            "cameras": [                // 所有已配置相机的状态
                {"serial": "...", "name": "...", "online": true},
                {"serial": "...", "name": "...", "online": false, "error": "..."}
            ],
            "stream_url": "ws://.../camera/stream",
            "frames_url": "ws://.../camera/frames"
        }
        """
        display_host = (
            "localhost" if self._server._host == "0.0.0.0" else self._server._host
        )
        if self._server._camera_manager is None:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "camera_status",
                        "available": False,
                        "camera_count": 0,
                        "cameras": [],
                        "stream_url": f"ws://{display_host}:{self._server._port}/camera/stream",
                        "frames_url": f"ws://{display_host}:{self._server._port}/camera/frames",
                    }
                )
            )
            return

        cameras_info = self._server._camera_manager.get_cameras_info()
        available = self._server._camera_manager.camera_count > 0
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "camera_status",
                    "available": available,
                    "camera_count": self._server._camera_manager.camera_count,
                    "cameras": cameras_info,
                    "stream_url": f"ws://{display_host}:{self._server._port}/camera/stream",
                    "frames_url": f"ws://{display_host}:{self._server._port}/camera/frames",
                }
            )
        )

    async def _handle_subscribe_camera_frames(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """订阅相机帧推送。
        请求: {"action": "subscribe_camera_frames"}
        成功后服务端持续推送: {"event": "camera_frames", "frames": [...]}
        """
        if self._server._camera_preview_session is None:
            try:
                self._server._camera_preview_session = (
                    self._server._services.camera_access.open("websocket-preview")
                )
            except Exception as exc:
                await websocket.send(
                    self._server._json_msg(
                        {
                            "event": "camera_error",
                            "message": f"相机资源不可用: {exc}",
                            "cameras": [],
                        }
                    )
                )
                return

        camera = self._server._camera_preview_session.camera
        if not camera.camera_count:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "camera_error",
                        "message": "所有配置相机均不可用",
                        "cameras": camera.get_cameras_info(),
                    }
                )
            )
            self._server._camera_preview_session.close()
            self._server._camera_preview_session = None
            return

        self._server._camera_frame_subs.add(websocket)
        await websocket.send(self._server._json_msg({"event": "camera_subscribed"}))

        if (
            self._server._camera_push_task is None
            or self._server._camera_push_task.done()
        ):
            self._server._camera_push_task = self._server._schedule_background_task(
                self._camera_push_loop(),
                name="WebSocketCameraPush",
            )
        logger.info("客户端订阅相机帧: %s", websocket.remote_address)

    async def _handle_unsubscribe_camera_frames(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """取消相机帧订阅。
        请求: {"action": "unsubscribe_camera_frames"}
        """
        self._server._camera_frame_subs.discard(websocket)
        await websocket.send(self._server._json_msg({"event": "camera_unsubscribed"}))
        if not self._server._camera_frame_subs:
            self._stop_camera_if_idle()

    async def _camera_push_loop(self) -> None:
        """后台任务：以 30fps 向所有订阅客户端推送相机帧。"""
        interval = 1.0 / 30
        try:
            while self._server._camera_frame_subs:
                session = self._server._camera_preview_session
                if session is not None and session.active:
                    jpegs = session.camera.get_latest_jpegs()
                    if jpegs:
                        payload = {
                            "event": "camera_frames",
                            "frames": [
                                {
                                    "serial": serial,
                                    "name": name,
                                    "index": idx,
                                    "data": base64.b64encode(jpeg).decode("ascii"),
                                }
                                for idx, (serial, name, jpeg) in enumerate(jpegs)
                            ],
                        }
                        await self._server._send_to_subscribers(
                            payload,
                            self._server._camera_frame_subs,
                        )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("相机预览推送失败: %s", exc)
        finally:
            self._server._camera_frame_subs.clear()
            self._server._camera_push_task = None
            self._stop_camera_if_idle()

    def _stop_camera_if_idle(self) -> None:
        """Release the preview lease after the last subscriber leaves."""
        if self._server._camera_frame_subs:
            return
        session = self._server._camera_preview_session
        self._server._camera_preview_session = None
        if session is not None:
            session.close()
