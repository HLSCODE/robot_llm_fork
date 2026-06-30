"""
RLBench格式数据保存器
将采集的帧数据保存为RLBench标准格式
"""
import os
import pickle
import cv2
import numpy as np
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# RLBench数据结构（简化版，仅导入必要的类）
try:
    from rlbench.backend.observation import Observation
    from rlbench.demo import Demo
    RLBENCH_AVAILABLE = True
except ImportError:
    RLBENCH_AVAILABLE = False
    logger.warning("rlbench库未安装，将使用简化版数据结构")
    
    # 简化版Observation类（兼容原RLBench格式）
    class Observation:
        """简化版Observation（字段与RLBench一致）"""
        def __init__(self, **kwargs):
            # 视觉字段
            self.left_shoulder_rgb = kwargs.get('left_shoulder_rgb')
            self.left_shoulder_depth = kwargs.get('left_shoulder_depth')
            self.left_shoulder_point_cloud = kwargs.get('left_shoulder_point_cloud')
            self.right_shoulder_rgb = kwargs.get('right_shoulder_rgb')
            self.right_shoulder_depth = kwargs.get('right_shoulder_depth')
            self.right_shoulder_point_cloud = kwargs.get('right_shoulder_point_cloud')
            self.overhead_rgb = kwargs.get('overhead_rgb')
            self.overhead_depth = kwargs.get('overhead_depth')
            self.overhead_point_cloud = kwargs.get('overhead_point_cloud')
            self.wrist_rgb = kwargs.get('wrist_rgb')
            self.wrist_depth = kwargs.get('wrist_depth')
            self.wrist_point_cloud = kwargs.get('wrist_point_cloud')
            self.front_rgb = kwargs.get('front_rgb')
            self.front_depth = kwargs.get('front_depth')
            self.front_point_cloud = kwargs.get('front_point_cloud')
            
            # Mask字段
            self.left_shoulder_mask = kwargs.get('left_shoulder_mask')
            self.right_shoulder_mask = kwargs.get('right_shoulder_mask')
            self.overhead_mask = kwargs.get('overhead_mask')
            self.wrist_mask = kwargs.get('wrist_mask')
            self.front_mask = kwargs.get('front_mask')
            
            # 机械臂字段
            self.joint_velocities = kwargs.get('joint_velocities')
            self.joint_positions = kwargs.get('joint_positions')
            self.joint_forces = kwargs.get('joint_forces')
            self.gripper_open = kwargs.get('gripper_open', 0.0)
            self.gripper_pose = kwargs.get('gripper_pose')
            self.gripper_matrix = kwargs.get('gripper_matrix')
            self.gripper_touch_forces = kwargs.get('gripper_touch_forces')
            self.gripper_joint_positions = kwargs.get('gripper_joint_positions')
            
            # 其他字段
            self.task_low_dim_state = kwargs.get('task_low_dim_state')
            self.ignore_collisions = kwargs.get('ignore_collisions', True)
            self.misc = kwargs.get('misc', {})
    
    # 简化版Demo类
    class Demo:
        """简化版Demo（包含Observation序列）"""
        def __init__(self, observations, random_seed=42):
            self._observations = observations
            self.random_seed = random_seed
            self.variation_number = 0
        
        def __len__(self):
            return len(self._observations)
        
        def __getitem__(self, i):
            return self._observations[i]


class RLBenchFormatter:
    """
    RLBench格式保存器
    
    保存结构：
    task_name/all_variations/episodes/episode{id}/
        ├── front_rgb/        {0.png, 1.png, ...}
        ├── front_depth/      {0.png, 1.png, ...}
        ├── low_dim_obs.pkl   (Demo对象)
        ├── variation_number.pkl
        └── variation_descriptions.pkl
    """
    
    def __init__(self, save_path: str = "data/demos"):
        """
        初始化保存器
        
        Args:
            save_path: 基础保存路径（如"data/demos"）
        """
        self._save_path = Path(save_path)
        logger.info(f"RLBenchFormatter初始化，保存路径: {self._save_path}")
    
    def save_episode(
        self,
        task: str,
        episode_id: int,
        frames: List,
        description: str,
        variation_id: int = 0,
    ) -> Dict[str, Any]:
        """
        保存单条episode
        
        Args:
            task: 任务名称（如"pick_bottle"）
            episode_id: Episode编号
            frames: FrameData列表
            description: 任务描述
            variation_id: Variation编号（默认0）
        
        Returns:
            {"success": bool, "path": str, "frames": int}
        """
        try:
            # 构建保存路径
            episode_path = self._save_path / task / "all_variations" / "episodes" / f"episode{episode_id}"
            
            # 创建目录
            self._create_episode_directories(episode_path)
            
            # 保存视觉数据（PNG）
            self._save_visual_data(episode_path, frames)
            
            # 构建Observation列表
            observations = self._build_observations(frames, description)
            
            # 保存低维状态（pkl）
            self._save_low_dim_obs(episode_path, observations, variation_id)
            
            # 保存元数据
            self._save_metadata(episode_path, description, variation_id)
            
            logger.info(f"episode {episode_id} 已保存到 {episode_path}")
            
            return {
                "success": True,
                "path": str(episode_path),
                "frames": len(frames),
            }
        
        except Exception as e:
            logger.error(f"保存episode {episode_id}失败: {e}")
            raise
    
    def _create_episode_directories(self, episode_path: Path):
        """创建episode目录结构"""
        episode_path.mkdir(parents=True, exist_ok=True)
        
        # 创建视觉数据目录
        (episode_path / "front_rgb").mkdir(exist_ok=True)
        (episode_path / "front_depth").mkdir(exist_ok=True)
    
    def _save_visual_data(self, episode_path: Path, frames: List):
        """保存视觉数据（PNG格式）"""
        front_rgb_path = episode_path / "front_rgb"
        front_depth_path = episode_path / "front_depth"
        
        for idx, frame in enumerate(frames):
            # RGB图像
            if frame.front_rgb is not None:
                rgb_file = front_rgb_path / f"{idx}.png"
                cv2.imwrite(str(rgb_file), frame.front_rgb)
            
            # Depth图像
            if frame.front_depth is not None:
                depth_file = front_depth_path / f"{idx}.png"
                cv2.imwrite(str(depth_file), frame.front_depth)
    
    def _build_observations(self, frames: List, description: str) -> List[Observation]:
        """构建Observation列表"""
        observations = []
        
        # 构建misc字典（相机参数等）
        misc = {}
        if frames and frames[0].camera_intrinsics is not None:
            misc['front_camera_intrinsics'] = frames[0].camera_intrinsics
            # 外参矩阵（简化版，先设为单位矩阵）
            misc['front_camera_extrinsics'] = np.eye(4)
            misc['front_camera_near'] = 0.5
            misc['front_camera_far'] = 4.5
        
        for frame in frames:
            # 构建Observation（字段与原系统一致）
            obs = Observation(
                # 视觉字段（仅front有数据）
                left_shoulder_rgb=None,
                left_shoulder_depth=None,
                left_shoulder_point_cloud=None,
                right_shoulder_rgb=None,
                right_shoulder_depth=None,
                right_shoulder_point_cloud=None,
                overhead_rgb=None,
                overhead_depth=None,
                overhead_point_cloud=None,
                wrist_rgb=None,
                wrist_depth=None,
                wrist_point_cloud=None,
                front_rgb=None,  # RGB已保存为PNG，这里设为None
                front_depth=None,  # Depth已保存为PNG，这里设为None
                front_point_cloud=None,
                
                # Mask字段（全部None）
                left_shoulder_mask=None,
                right_shoulder_mask=None,
                overhead_mask=None,
                wrist_mask=None,
                front_mask=None,
                
                # 机械臂字段
                joint_velocities=frame.joint_velocities,
                joint_positions=frame.joint_positions,
                joint_forces=frame.joint_forces,
                gripper_open=frame.gripper_open,
                gripper_pose=frame.gripper_pose,
                gripper_matrix=frame.gripper_matrix,
                gripper_touch_forces=None,
                gripper_joint_positions=frame.gripper_joint_positions,
                
                # 其他字段
                task_low_dim_state=None,
                ignore_collisions=True,
                misc=misc,
            )
            
            observations.append(obs)
        
        return observations
    
    def _save_low_dim_obs(self, episode_path: Path, observations: List[Observation], variation_id: int):
        """保存低维状态（Demo对象）"""
        # 构建Demo对象
        demo = Demo(observations, random_seed=42)
        demo.variation_number = variation_id
        
        # 保存为pkl
        low_dim_obs_path = episode_path / "low_dim_obs.pkl"
        with open(low_dim_obs_path, 'wb') as f:
            pickle.dump(demo, f)
        
        # 保存variation_number
        variation_number_path = episode_path / "variation_number.pkl"
        with open(variation_number_path, 'wb') as f:
            pickle.dump(variation_id, f)
    
    def _save_metadata(self, episode_path: Path, description: str, variation_id: int):
        """保存元数据（任务描述）"""
        # 语言描述（与原系统一致）
        descriptions = description.split(",")  # 支持多个描述（逗号分隔）
        descriptions_path = episode_path / "variation_descriptions.pkl"
        with open(descriptions_path, 'wb') as f:
            pickle.dump(descriptions, f)