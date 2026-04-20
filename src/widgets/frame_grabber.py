# -*- coding: utf-8 -*-
"""
OnDemandFrameGrabber - 按需从 RealSense D435i 相机取帧

核心：RealSense SDK 的 wait_for_frames() 在任何线程直接调用都是安全的。
USB urb 回调由 RealSense 内部线程处理，数据通过队列传递，不依赖 Qt 事件循环。

架构：
1. GrabberThread：独立线程，循环调用 pipeline.wait_for_frames() 取帧，存入 Queue
2. grab(timeout=N)：启动线程，等待 Queue 中有帧，最长 N 秒
3. 取到帧后立即停止 pipeline，唤醒 grab() 返回

RealSense SDK 在 pipeline.start() 后自动创建内部线程负责 USB urb 通信，
收到帧后放入 SDK 内部缓冲区，wait_for_frames() 从缓冲区取出。
"""

import threading
import queue
import time
import numpy as np

try:
    import pyrealsense2 as rs
    REALSENSE_AVAILABLE = True
except ImportError:
    REALSENSE_AVAILABLE = False
    rs = None


class _GrabberThread(threading.Thread):
    """独立取帧线程，循环取 RealSense 帧并存入队列。"""

    def __init__(self, device_sn=None):
        super().__init__(daemon=True)
        self._device_sn = device_sn
        self._pipeline = None
        self._color_intr = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._error_msg = None
        self._started_event = threading.Event()  # 等待 pipeline 初始化完成

    def run(self):
        """线程主循环：取帧，直到收到停止信号。"""
        try:
            self._pipeline = rs.pipeline()
            cfg = rs.config()

            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)

            if self._device_sn:
                cfg.enable_device(self._device_sn)

            profile = self._pipeline.start(cfg)

            intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
            self._color_intr = {
                "fx": intr.fx,
                "fy": intr.fy,
                "ppx": intr.ppx,
                "ppy": intr.ppy,
            }

            self._started_event.set()

            while not self._stop_event.is_set():
                try:
                    frames = self._pipeline.wait_for_frames(500)
                    if self._stop_event.is_set():
                        break
                    color_frame = frames.get_color_frame()
                    depth_frame = frames.get_depth_frame()
                    if not color_frame or not depth_frame:
                        continue

                    color = np.asanyarray(color_frame.get_data())
                    depth = np.asanyarray(depth_frame.get_data())

                    try:
                        self._frame_queue.put_nowait((color, depth, self._color_intr))
                    except queue.Full:
                        try:
                            self._frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                        self._frame_queue.put_nowait((color, depth, self._color_intr))

                except RuntimeError as e:
                    err = str(e)
                    if "didn't arrive" in err or "did not arrive" in err:
                        continue
                    self._error_msg = err
                    break
                except Exception as e:
                    self._error_msg = str(e)
                    break

        except Exception as e:
            self._error_msg = str(e)
        finally:
            self._started_event.set()
            self._cleanup()

    def _cleanup(self):
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass
            self._pipeline = None

    def get_frame(self, timeout=None):
        """
        尝试从队列取出帧。

        Args:
            timeout: 等待秒数，None 表示立即返回

        Returns:
            (color, depth, intrinsics) 或 None（超时或有错）
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_error(self):
        return self._error_msg

    def stop(self, timeout=3):
        self._stop_event.set()
        self.join(timeout=timeout)


class OnDemandFrameGrabber:
    """
    按需取帧封装，每次 grab() 启动 _GrabberThread，取一帧后立即停止。
    """

    def __init__(self, device_sn=None):
        if not REALSENSE_AVAILABLE:
            raise RuntimeError("pyrealsense2 未安装，无法获取深度相机帧")

        self._device_sn = device_sn
        self._thread = None

    def grab(self, timeout=10):
        """
        启动 RealSense，取一帧，返回后立即停止 pipeline。

        Args:
            timeout: 最大等待时间（秒）

        Returns:
            (color, depth, intrinsics)
            - color:      HxWx3 uint8，BGR 格式
            - depth:      HxW uint16，深度值（mm）
            - intrinsics: dict，含 fx, fy, ppx, ppy

        Raises:
            RuntimeError: 初始化失败或取帧超时
        """
        self._thread = _GrabberThread(self._device_sn)
        self._thread.start()

        # 等待 pipeline 初始化完成（最多 5 秒）
        self._thread._started_event.wait(timeout=5)
        if self._thread.get_error():
            err = self._thread.get_error()
            self._thread.stop()
            raise RuntimeError(f"Pipeline 初始化失败: {err}")

        deadline = time.time() + timeout
        remaining = timeout

        while time.time() < deadline:
            result = self._thread.get_frame(timeout=min(0.5, remaining))
            remaining = deadline - time.time()

            if result is not None:
                self._thread.stop(timeout=3)
                return result

            if self._thread.get_error():
                err = self._thread.get_error()
                self._thread.stop(timeout=3)
                raise RuntimeError(err)

            if remaining <= 0:
                break

        self._thread.stop(timeout=3)
        raise RuntimeError("取帧超时，未在指定时间内获得有效帧")

    def _cleanup(self):
        """兼容旧调用方（execution.py）：同步停止。"""
        if self._thread is not None:
            self._thread.stop(timeout=3)
            self._thread = None
