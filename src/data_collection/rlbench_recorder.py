"""
RLBench数据采集器
实时采集遥操作数据并保存为RLBench格式
"""
import threading
import time
import logging
import copy
import numpy as np
from typing import Optional, Dict, Any, List
from datetime import datetime

from ..device_runtime import ArmId, ArmStateReader, DepthCameraSource

logger = logging.getLogger(__name__)


class FrameData:
    """
    单帧数据容器
    存储某一时刻的完整状态（相机+机械臂）
    """
    def __init__(self, timestamp: float):
        self.timestamp = timestamp
        self.front_rgb: Optional[np.ndarray] = None
        self.front_depth: Optional[np.ndarray] = None
        self.camera_intrinsics: Optional[np.ndarray] = None
        
        # 机械臂状态（简化版，先实现核心字段）
        self.joint_positions: Optional[np.ndarray] = None  # 7个关节角度
        self.joint_velocities: Optional[np.ndarray] = None
        self.gripper_open: float = 0.0  # 0.0 or 1.0
        self.gripper_pose: Optional[np.ndarray] = None  # 7维位姿(x,y,z,qx,qy,qz,qw)
        
        # 其他字段（后续完善）
        self.joint_forces: Optional[np.ndarray] = None
        self.gripper_matrix: Optional[np.ndarray] = None
        self.gripper_joint_positions: Optional[np.ndarray] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（便于序列化）"""
        return {
            'timestamp': self.timestamp,
            'front_rgb': self.front_rgb,
            'front_depth': self.front_depth,
            'camera_intrinsics': self.camera_intrinsics,
            'joint_positions': self.joint_positions,
            'joint_velocities': self.joint_velocities,
            'gripper_open': self.gripper_open,
            'gripper_pose': self.gripper_pose,
        }


class RLBenchRecorder:
    """
    RLBench格式数据采集器
    
    功能：
    1. 30Hz定时采集相机帧+机械臂状态
    2. Episode管理（自动编号）
    3. 与WebSocket服务端集成
    """
    
    def __init__(
        self,
        robot_state_reader: ArmStateReader,
        camera_source: DepthCameraSource,
        config,
    ):
        """
        初始化采集器
        
        Args:
            robot_state_reader: 项目级机械臂状态能力
            camera_source: 项目级深度相机能力
            config: DataCollectionConfig实例
        """
        self._robot_state_reader = robot_state_reader
        self._camera_source = camera_source
        self._config = config
        
        # 采集线程
        self._collect_thread: Optional[threading.Thread] = None
        self._collecting = False
        
        # 当前采集的数据缓存
        self._current_frames: List[FrameData] = []
        self._frames_lock = threading.Lock()
        
        # 会话状态
        self._session_active = False
        self._task_name: Optional[str] = None
        self._description: Optional[str] = None
        self._next_episode_id: int = 0
        
        logger.info(f"RLBenchRecorder初始化完成: {config}")
    
    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------
    
    def start_session(self, task: str, description: str) -> Dict[str, Any]:
        """
        开始数据采集会话
        
        Args:
            task: 任务名称（如"pick_bottle"）
            description: 任务描述（如"抓取瓶子放到桌上"）
        
        Returns:
            {"success": bool, "next_episode_id": int, "message": str}
        """
        self._session_active = True
        self._task_name = task
        self._description = description
        
        # 计算下一个episode编号（跳过已存在的）
        self._next_episode_id = self._calculate_next_episode_id()
        
        logger.info(f"数据采集会话已启动: task={task}, next_episode_id={self._next_episode_id}")
        
        return {
            "success": True,
            "next_episode_id": self._next_episode_id,
            "message": f"会话已启动，下一个episode编号为{self._next_episode_id}"
        }
    
    def _calculate_next_episode_id(self) -> int:
        """
        计算下一个episode编号
        检查已存在的episode目录，跳过已存在的编号
        """
        import os
        from pathlib import Path
        
        save_path = Path(self._config.SAVE_PATH) / self._task_name / "all_variations" / "episodes"
        
        if not save_path.exists():
            return 0
        
        # 查找已存在的最大编号
        existing_ids = []
        for episode_dir in save_path.iterdir():
            if episode_dir.is_dir() and episode_dir.name.startswith("episode"):
                try:
                    id_str = episode_dir.name.replace("episode", "")
                    existing_ids.append(int(id_str))
                except ValueError:
                    continue
        
        if existing_ids:
            return max(existing_ids) + 1
        else:
            return 0
    
    def end_session(self) -> Dict[str, Any]:
        """
        结束数据采集会话
        
        Returns:
            {"success": bool, "message": str}
        """
        self._session_active = False
        self._task_name = None
        self._description = None
        
        logger.info("数据采集会话已结束")
        
        return {
            "success": True,
            "message": "会话已结束"
        }
    
    # ------------------------------------------------------------------
    # Episode管理
    # ------------------------------------------------------------------
    
    def start_recording(self) -> Dict[str, Any]:
        """
        开始记录单条episode
        
        Returns:
            {"success": bool, "episode_id": int, "message": str}
        """
        if not self._session_active:
            return {
                "success": False,
                "message": "会话未启动，请先调用start_session"
            }
        
        # 清空缓存
        with self._frames_lock:
            self._current_frames.clear()
        
        # 启动采集线程
        self._collecting = True
        self._collect_thread = threading.Thread(
            target=self._collect_loop,
            daemon=True,
            name="RLBenchRecorder"
        )
        self._collect_thread.start()
        
        episode_id = self._next_episode_id
        
        logger.info(f"开始记录episode {episode_id}")
        
        return {
            "success": True,
            "episode_id": episode_id,
            "message": f"episode {episode_id} 开始记录"
        }
    
    def stop_recording(self) -> Dict[str, Any]:
        """
        结束记录并保存episode
        
        Returns:
            {"success": bool, "episode_id": int, "frames": int, "message": str}
        """
        if not self._collecting:
            return {
                "success": False,
                "message": "未在记录状态"
            }
        
        # 停止采集线程
        self._collecting = False
        if self._collect_thread:
            self._collect_thread.join(timeout=2.0)
            self._collect_thread = None
        
        # 获取采集的帧数
        with self._frames_lock:
            frames_copy = copy.deepcopy(self._current_frames)
            frame_count = len(frames_copy)
        
        episode_id = self._next_episode_id
        
        # 保存数据
        from .rlbench_formatter import RLBenchFormatter
        formatter = RLBenchFormatter(self._config.SAVE_PATH)
        
        try:
            save_result = formatter.save_episode(
                task=self._task_name,
                episode_id=episode_id,
                frames=frames_copy,
                description=self._description,
            )
            
            # 更新下一个episode编号
            self._next_episode_id += 1
            
            logger.info(f"episode {episode_id} 已保存，共{frame_count}帧")
            
            return {
                "success": True,
                "episode_id": episode_id,
                "frames": frame_count,
                "message": f"episode {episode_id} 已保存，共{frame_count}帧"
            }
        except Exception as e:
            logger.error(f"保存episode {episode_id}失败: {e}")
            return {
                "success": False,
                "episode_id": episode_id,
                "frames": frame_count,
                "message": f"保存失败: {str(e)}"
            }
    
    # ------------------------------------------------------------------
    # 数据采集循环
    # ------------------------------------------------------------------
    
    def _collect_loop(self):
        """
        30Hz数据采集循环（后台线程）
        """
        dt = 1.0 / self._config.FPS
        logger.info(f"数据采集线程已启动，频率{self._config.FPS}Hz")
        
        while self._collecting:
            start_time = time.time()
            
            try:
                # 获取当前状态
                frame_data = self._get_current_frame()
                
                if frame_data:
                    # 添加到缓存
                    with self._frames_lock:
                        self._current_frames.append(frame_data)
                
            except Exception as e:
                logger.error(f"采集帧数据失败: {e}")
            
            # 控制频率
            elapsed = time.time() - start_time
            sleep_time = max(0, dt - elapsed)
            time.sleep(sleep_time)
        
        logger.info("数据采集线程已停止")
    
    def _get_current_frame(self) -> Optional[FrameData]:
        """
        获取当前时刻的完整状态
        
        Returns:
            FrameData实例，如果获取失败返回None
        """
        timestamp = time.time()
        frame_data = FrameData(timestamp)
        
        try:
            # 1. 获取相机数据
            if (
                self._camera_source
                and self._camera_source.is_running
                and self._camera_source.camera_count > 0
            ):
                # 使用配置指定的相机索引
                # 先尝试从get_cameras_info()获取相机列表
                cameras_info = self._camera_source.get_cameras_info()
                
                if cameras_info and len(cameras_info) > 0:
                    # 确定使用的相机索引（默认第一个）
                    camera_index = min(self._config.CAMERA_INDEX, len(cameras_info) - 1)
                    
                    # 获取相机名称或序列号
                    target_camera = cameras_info[camera_index]
                    camera_name = target_camera.get("name")
                    camera_serial = target_camera.get("serial")
                    
                    # 尝试使用名称或序列号获取数据
                    raw_frames = self._camera_source.get_latest_raw_frames(
                        camera_name or camera_serial
                    )
                    
                    if raw_frames:
                        # raw_frames格式: (color_bgr, depth_uint16, intrinsics_dict)
                        color_bgr, depth_uint16, intrinsics_dict = raw_frames
                        
                        frame_data.front_rgb = color_bgr
                        frame_data.front_depth = depth_uint16
                        
                        # 内参矩阵
                        if intrinsics_dict:
                            fx = intrinsics_dict.get('fx', 0)
                            fy = intrinsics_dict.get('fy', 0)
                            ppx = intrinsics_dict.get('ppx', 0)
                            ppy = intrinsics_dict.get('ppy', 0)
                            frame_data.camera_intrinsics = np.array([
                                [fx, 0, ppx],
                                [0, fy, ppy],
                                [0, 0, 1]
                            ])
                    else:
                        logger.warning(f"无法获取相机 {camera_name} ({camera_serial}) 的数据")
                else:
                    logger.warning("相机管理器中没有在线相机")
            
            # 2. 获取机械臂状态（简化版）
            if self._robot_state_reader:
                state = self._robot_state_reader.try_read_arm_state(
                    ArmId.LEFT
                )
                if state is not None:
                    if state.joints is not None:
                        frame_data.joint_positions = np.array(
                            state.joints.positions_deg
                        )
                    frame_data.joint_velocities = None
                    pose = state.pose.to_list()
                    frame_data.gripper_pose = np.array([*pose, 0.0])
                    frame_data.gripper_open = 0.0
                    frame_data.joint_forces = None
                    frame_data.gripper_matrix = None
                    frame_data.gripper_joint_positions = None
            
            return frame_data
        
        except Exception as e:
            logger.error(f"获取当前帧失败: {e}")
            return None
