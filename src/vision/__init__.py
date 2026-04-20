"""视觉抓取算法"""

try:
    from .interface import vertical_catch
except ImportError:
    vertical_catch = None


def __getattr__(name):
    if name == "VisionCaptureGUIAction":
        try:
            from .capture_gui import VisionCaptureGUIAction
        except ImportError:
            VisionCaptureGUIAction = None
        globals()["VisionCaptureGUIAction"] = VisionCaptureGUIAction
        return VisionCaptureGUIAction
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
