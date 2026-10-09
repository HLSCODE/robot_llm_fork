# Build both entry points into one directory sharing their native libraries.
from pathlib import Path
import os
import json

from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, copy_metadata,
)
from PyInstaller.config import CONF
from scripts.build_windows_portable import shorten_license_paths

os.environ["YOLO_AUTOINSTALL"] = "false"

root = Path(SPECPATH).resolve().parents[1]
datas = collect_data_files("src") + copy_metadata("robot-llm")
binaries = []
hiddenimports = ["PySide6.QtMultimedia"]
# Include project lazy exports without importing hardware modules at build time.
hiddenimports += [
    ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
    for path in (root / "src").rglob("*.py")
]
# These providers load modules/native libraries dynamically. Do not import or
# instantiate robot clients during the build.
for package in (
    "tj_robot_proj", "Robotic_Arm", "funasr", "sherpa_onnx", "textual",
):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

# Do not enumerate training/export/tracking modules: some import optional
# frameworks or attempt to install extras. Runtime imports are analyzed normally.
for package in ("modelscope", "ultralytics"):
    datas += collect_data_files(package)
    datas += copy_metadata(package)
    binaries += collect_dynamic_libs(package)
hiddenimports += ["modelscope.hub.snapshot_download", "ultralytics.nn.tasks"]

datas += [
    (str(root / "assets/ref_audio" / name), "assets/ref_audio")
    for name in ("ref_minicpm_signature.wav", "ref_en_dlc_1.wav")
]
icon = str(root / "src/gui/assets/app/app-icon.ico")
common = dict(
    pathex=[str(root)], datas=datas, binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["pytest", "mypy", "ruff", "IPython", "jupyter", "PyQt5", "PyQt6"],
)
application = Analysis([str(root / "packaging/windows/portable_entry.py")], **common)
application.datas, license_paths = shorten_license_paths(application.datas)
license_index = Path(CONF["workpath"]) / "license-paths.json"
license_index.write_text(json.dumps(license_paths, indent=2), encoding="utf-8")
application.datas.append(("_licenses/original-paths.json", str(license_index), "DATA"))
pyz = PYZ(application.pure)
gui_exe = EXE(
    pyz, application.scripts, [], exclude_binaries=True,
    name="robot-llm", console=False, icon=icon, upx=False,
)
init_exe = EXE(
    pyz, application.scripts, [("u", None, "OPTION")],
    exclude_binaries=True, name="robot-init", console=True, icon=icon, upx=False,
)
COLLECT(
    gui_exe, init_exe, application.binaries, application.datas,
    name="robot-llm-windows-x86_64", upx=False,
)
