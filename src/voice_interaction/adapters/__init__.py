"""
External-system adapters for voice interaction.
"""
from .cameras import CameraCaptureError, CameraProvider, CamerasModuleProvider

__all__ = [
    "CameraCaptureError",
    "CameraProvider",
    "CamerasModuleProvider",
]
